"""
Shopify Admin API provider.

Supports:
- OAuth2 installation flow (for public apps) OR private app credentials
- Orders: list, get, fulfill, cancel, refund
- Products: list, get, create, update
- Inventory: get levels, adjust, set
- Customers: list, get
- Competitive pricing hooks
- Webhook registration / verification

Credentials dict expected:
  {
    "shop_domain": "mystore.myshopify.com",
    "access_token": "shpua_xxx",      # private app or OAuth token
    "api_key": "...",                  # optional, for OAuth apps
    "api_secret": "..."                # optional, for webhook HMAC verification
  }
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator, Optional
from urllib.parse import urlencode, urljoin

import httpx
from starlette.requests import Request

from core.config import settings
from core.utils.log import BackLog
from providers.base import BaseProvider
from schemas.commerce import (
    Address,
    FulfillmentRequest,
    InventoryAdjustment,
    InventoryLevel,
    InventorySet,
    LineItem,
    OrderListOptions,
    OrderSchema,
    ProductListOptions,
    ProductSchema,
    ProductVariantSchema,
    PriceUpdate,
    RefundRequest,
)

logger = logging.getLogger(__name__)

API_VERSION = settings.SHOPIFY_API_VERSION


class ShopifyProvider(BaseProvider):
    """Shopify Admin REST API connector."""

    def get_provider_info(self) -> dict:
        return {
            "provider": self.plugin_name.lower(),
            "short_name": "Shopify",
            "provider_description": "Shopify Store",
            "provider_icon_url": "/shopify.svg",
        }

    # ------------------------------------------------------------------
    # HTTP client helpers
    # ------------------------------------------------------------------

    def _base_url(self, shop_domain: str) -> str:
        domain = shop_domain.rstrip("/")
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        return f"{domain}/admin/api/{API_VERSION}"

    def _client(self, credentials: dict) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url(credentials["shop_domain"]),
            headers={
                "X-Shopify-Access-Token": credentials["access_token"],
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict,
        key: str,
    ) -> AsyncGenerator[list, None]:
        """Cursor-based pagination through Shopify REST endpoints."""
        while True:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get(key, [])
            yield items
            # Follow Link header for next page
            link_header = resp.headers.get("Link", "")
            if 'rel="next"' not in link_header:
                break
            # Extract page_info from Link header
            page_info = None
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    url_part = part.split(";")[0].strip().strip("<>")
                    for qp in url_part.split("?")[1].split("&"):
                        if qp.startswith("page_info="):
                            page_info = qp.split("=", 1)[1]
            if not page_info:
                break
            params = {"limit": params.get("limit", 250), "page_info": page_info}

    # ------------------------------------------------------------------
    # Auth / OAuth
    # ------------------------------------------------------------------

    async def link_provider(self, redirect_url: str, request: Request) -> Any:
        """Generate Shopify OAuth install URL."""
        shop = request.query_params.get("shop")
        if not shop:
            return {"error": "Missing shop parameter"}

        scopes = ",".join([
            "read_products", "write_products",
            "read_orders", "write_orders",
            "read_inventory", "write_inventory",
            "read_customers", "write_customers",
            "read_fulfillments", "write_fulfillments",
            "read_shipping", "write_shipping",
            "read_analytics",
        ])
        nonce = hashlib.sha256(f"{shop}{settings.SHOPIFY_API_SECRET}".encode()).hexdigest()[:16]
        request.session["shopify_nonce"] = nonce
        request.session["shopify_redirect"] = redirect_url

        params = {
            "client_id": settings.SHOPIFY_API_KEY,
            "scope": scopes,
            "redirect_uri": f"{settings.WEBHOOK_BASE_URL}/providers/shopify_auth",
            "state": nonce,
        }
        return {"redirect_url": f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"}

    async def get_access_token(self, request: Request) -> Any:
        """Exchange OAuth code for access token and register the store."""
        from starlette.responses import RedirectResponse
        from urllib.parse import urlencode

        code = request.query_params.get("code")
        shop = request.query_params.get("shop")
        state = request.query_params.get("state")

        stored_nonce = request.session.get("shopify_nonce")
        if state != stored_nonce:
            return {"error": "Invalid state/nonce"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://{shop}/admin/oauth/access_token",
                json={
                    "client_id": settings.SHOPIFY_API_KEY,
                    "client_secret": settings.SHOPIFY_API_SECRET,
                    "code": code,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        user_id = request.session.get("user_id", "")

        credentials = {
            "shop_domain": shop,
            "access_token": token_data["access_token"],
            "scope": token_data.get("scope", ""),
        }

        if user_id:
            # Auto-save credentials to Firebase — no manual update_provider_info needed
            try:
                import json
                from db.cruds.users import update_user
                from db.schemas.users import UsersSchema
                update_user(
                    user=UsersSchema(id=user_id, email=""),
                    provider_name="shopifyprovider",
                    key=shop,
                    content=json.dumps(credentials),
                )
            except Exception as e:
                logger.warning("Auto-save Shopify credentials failed (non-fatal): %s", e)

            # Register store row in Postgres
            try:
                from db.cruds.stores import get_or_create_store
                await get_or_create_store(
                    user_id=user_id,
                    provider="shopifyprovider",
                    identifier=shop,
                    shop_domain=shop,
                )
            except Exception as e:
                logger.warning("Store registration failed (non-fatal): %s", e)

        redirect_url = request.session.get("shopify_redirect", "/")
        return_params = {
            "provider": "shopifyprovider",
            "identifier_name": shop,  # the key to use in all subsequent API calls
            "shop_domain": shop,
            "connected": "true",
        }
        return RedirectResponse(url=redirect_url + "?" + urlencode(return_params))

    async def disconnect(self, request: Request) -> None:
        pass

    def verify_webhook(self, data: bytes, hmac_header: str) -> bool:
        """Verify Shopify webhook HMAC signature."""
        secret = settings.SHOPIFY_WEBHOOK_SECRET or settings.SHOPIFY_API_SECRET
        if not secret:
            return False
        digest = hmac.new(secret.encode(), data, hashlib.sha256).digest()
        import base64
        computed = base64.b64encode(digest).decode()
        return hmac.compare_digest(computed, hmac_header)

    async def register_webhooks(self, credentials: dict) -> list[dict]:
        """Register all required webhooks with Shopify."""
        topics = [
            "orders/create",
            "orders/updated",
            "orders/cancelled",
            "orders/fulfilled",
            "orders/refunded",
            "products/create",
            "products/update",
            "products/delete",
            "inventory_levels/update",
            "app/uninstalled",
        ]
        base = f"{settings.WEBHOOK_BASE_URL}/webhooks/shopify"
        results = []
        async with self._client(credentials) as client:
            for topic in topics:
                resp = await client.post(
                    "/webhooks.json",
                    json={"webhook": {
                        "topic": topic,
                        "address": f"{base}/{topic.replace('/', '_')}",
                        "format": "json",
                    }},
                )
                results.append({"topic": topic, "status": resp.status_code})
        return results

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def _parse_product(self, raw: dict) -> ProductSchema:
        variants = [
            ProductVariantSchema(
                external_id=str(v["id"]),
                sku=v.get("sku"),
                title=v.get("title", ""),
                price=Decimal(str(v.get("price", "0"))),
                compare_at_price=Decimal(str(v["compare_at_price"])) if v.get("compare_at_price") else None,
                inventory_quantity=v.get("inventory_quantity", 0),
                barcode=v.get("barcode"),
                option1=v.get("option1"),
                option2=v.get("option2"),
                option3=v.get("option3"),
            )
            for v in raw.get("variants", [])
        ]
        images = [img.get("src", "") for img in raw.get("images", [])]
        tags = [t.strip() for t in raw.get("tags", "").split(",") if t.strip()]

        return ProductSchema(
            external_id=str(raw["id"]),
            title=raw.get("title", ""),
            description=raw.get("body_html"),
            vendor=raw.get("vendor"),
            product_type=raw.get("product_type"),
            tags=tags,
            images=images,
            variants=variants,
            is_active=raw.get("status", "active") == "active",
        )

    async def get_all_products(
        self,
        credentials: dict,
        options: Optional[ProductListOptions] = None,
    ) -> list[ProductSchema]:
        options = options or ProductListOptions()
        params: dict = {"limit": options.limit}
        if options.product_type:
            params["product_type"] = options.product_type
        if options.vendor:
            params["vendor"] = options.vendor
        if options.updated_at_min:
            params["updated_at_min"] = options.updated_at_min.isoformat()

        all_products: list[ProductSchema] = []
        async with self._client(credentials) as client:
            async for page in self._paginate(client, "/products.json", params, "products"):
                for raw in page:
                    all_products.append(self._parse_product(raw))
        return all_products

    async def get_product(self, credentials: dict, product_id: str) -> Optional[ProductSchema]:
        async with self._client(credentials) as client:
            resp = await client.get(f"/products/{product_id}.json")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return self._parse_product(resp.json()["product"])

    async def update_product_price(
        self, credentials: dict, updates: list[PriceUpdate]
    ) -> list[dict]:
        results = []
        async with self._client(credentials) as client:
            for update in updates:
                payload = {"variant": {"id": update.variant_id, "price": str(update.price)}}
                if update.compare_at_price is not None:
                    payload["variant"]["compare_at_price"] = str(update.compare_at_price)
                resp = await client.put(f"/variants/{update.variant_id}.json", json=payload)
                resp.raise_for_status()
                results.append({
                    "variant_id": update.variant_id,
                    "new_price": str(update.price),
                    "status": "updated",
                })
        return results

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _parse_order(self, raw: dict) -> OrderSchema:
        line_items = [
            LineItem(
                external_id=str(li["id"]),
                product_id=str(li.get("product_id", "")),
                variant_id=str(li.get("variant_id", "")) if li.get("variant_id") else None,
                sku=li.get("sku"),
                title=li.get("title", ""),
                quantity=li.get("quantity", 0),
                price=Decimal(str(li.get("price", "0"))),
                total_discount=Decimal(str(li.get("total_discount", "0"))),
                fulfillment_status=li.get("fulfillment_status"),
                fulfillable_quantity=li.get("fulfillable_quantity", 0),
            )
            for li in raw.get("line_items", [])
        ]

        shipping_addr = None
        if raw.get("shipping_address"):
            sa = raw["shipping_address"]
            shipping_addr = Address(
                first_name=sa.get("first_name"),
                last_name=sa.get("last_name"),
                company=sa.get("company"),
                address1=sa.get("address1"),
                address2=sa.get("address2"),
                city=sa.get("city"),
                province=sa.get("province"),
                zip=sa.get("zip"),
                country=sa.get("country"),
                country_code=sa.get("country_code"),
                phone=sa.get("phone"),
            )

        customer = raw.get("customer", {}) or {}
        ordered_at = None
        if raw.get("created_at"):
            try:
                ordered_at = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        return OrderSchema(
            external_id=str(raw["id"]),
            order_number=str(raw.get("order_number", "")),
            status=raw.get("financial_status", "pending"),
            fulfillment_status=raw.get("fulfillment_status"),
            total_price=Decimal(str(raw.get("total_price", "0"))),
            subtotal_price=Decimal(str(raw.get("subtotal_price", "0"))) if raw.get("subtotal_price") else None,
            total_tax=Decimal(str(raw.get("total_tax", "0"))) if raw.get("total_tax") else None,
            total_discounts=Decimal(str(raw.get("total_discounts", "0"))) if raw.get("total_discounts") else None,
            currency=raw.get("currency", "USD"),
            customer_id=str(customer.get("id", "")) if customer.get("id") else None,
            customer_email=customer.get("email") or raw.get("email"),
            customer_name=f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or None,
            shipping_address=shipping_addr,
            line_items=line_items,
            tags=raw.get("tags"),
            note=raw.get("note"),
            source_channel=raw.get("source_name"),
            ordered_at=ordered_at,
        )

    async def get_orders(
        self,
        credentials: dict,
        options: Optional[OrderListOptions] = None,
    ) -> list[OrderSchema]:
        options = options or OrderListOptions()
        since = datetime.now(timezone.utc) - timedelta(hours=options.since_hours)
        params: dict = {
            "limit": options.limit,
            "status": options.status,
            "created_at_min": since.isoformat(),
        }
        if options.fulfillment_status:
            params["fulfillment_status"] = options.fulfillment_status

        all_orders: list[OrderSchema] = []
        async with self._client(credentials) as client:
            async for page in self._paginate(client, "/orders.json", params, "orders"):
                for raw in page:
                    all_orders.append(self._parse_order(raw))
        return all_orders

    async def get_order(self, credentials: dict, order_id: str) -> Optional[OrderSchema]:
        async with self._client(credentials) as client:
            resp = await client.get(f"/orders/{order_id}.json")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return self._parse_order(resp.json()["order"])

    async def fulfill_order(self, credentials: dict, request: FulfillmentRequest) -> dict:
        payload: dict = {
            "fulfillment": {
                "notify_customer": request.notify_customer,
                "line_items_by_fulfillment_order": [],
            }
        }
        if request.tracking_number:
            payload["fulfillment"]["tracking_info"] = {
                "number": request.tracking_number,
                "company": request.tracking_company,
                "url": request.tracking_url,
            }
        if request.location_id:
            payload["fulfillment"]["location_id"] = request.location_id

        async with self._client(credentials) as client:
            # Get fulfillment orders first
            resp = await client.get(f"/orders/{request.order_id}/fulfillment_orders.json")
            resp.raise_for_status()
            fo_list = resp.json().get("fulfillment_orders", [])

            line_items_by_fo = []
            for fo in fo_list:
                if fo.get("status") not in ("open", "in_progress"):
                    continue
                li = [{"id": li["id"]} for li in fo.get("line_items", [])]
                if request.line_item_ids:
                    li = [l for l in li if str(l["id"]) in request.line_item_ids]
                if li:
                    line_items_by_fo.append({
                        "fulfillment_order_id": fo["id"],
                        "fulfillment_order_line_items": li,
                    })

            if not line_items_by_fo:
                return {"status": "nothing_to_fulfill"}

            payload["fulfillment"]["line_items_by_fulfillment_order"] = line_items_by_fo
            resp = await client.post("/fulfillments.json", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def cancel_order(
        self, credentials: dict, order_id: str, reason: Optional[str] = None
    ) -> dict:
        async with self._client(credentials) as client:
            payload = {}
            if reason:
                payload["reason"] = reason
            resp = await client.post(f"/orders/{order_id}/cancel.json", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def refund_order(self, credentials: dict, request: RefundRequest) -> dict:
        # Calculate refund first
        async with self._client(credentials) as client:
            calc_payload: dict = {
                "refund": {
                    "shipping": request.shipping or {},
                    "refund_line_items": request.line_items or [],
                }
            }
            calc = await client.post(
                f"/orders/{request.order_id}/refunds/calculate.json",
                json=calc_payload,
            )
            calc.raise_for_status()
            refund_data = calc.json()["refund"]

            # Apply refund
            refund_payload = {
                "refund": {
                    "notify": request.notify,
                    "note": request.note or "",
                    "shipping": refund_data.get("shipping", {}),
                    "refund_line_items": refund_data.get("refund_line_items", []),
                    "transactions": refund_data.get("transactions", []),
                }
            }
            resp = await client.post(
                f"/orders/{request.order_id}/refunds.json",
                json=refund_payload,
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    async def get_inventory_levels(
        self, credentials: dict, location_ids: Optional[list[str]] = None
    ) -> list[InventoryLevel]:
        params: dict = {"limit": 250}
        if location_ids:
            params["location_ids"] = ",".join(location_ids)

        levels: list[InventoryLevel] = []
        async with self._client(credentials) as client:
            async for page in self._paginate(
                client, "/inventory_levels.json", params, "inventory_levels"
            ):
                for item in page:
                    levels.append(InventoryLevel(
                        inventory_item_id=str(item["inventory_item_id"]),
                        location_id=str(item["location_id"]),
                        available=item.get("available", 0) or 0,
                    ))
        return levels

    async def adjust_inventory(
        self, credentials: dict, adjustment: InventoryAdjustment
    ) -> dict:
        async with self._client(credentials) as client:
            resp = await client.post(
                "/inventory_levels/adjust.json",
                json={
                    "location_id": adjustment.location_id,
                    "inventory_item_id": adjustment.inventory_item_id,
                    "available_adjustment": adjustment.available_adjustment,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def set_inventory(self, credentials: dict, setting: InventorySet) -> dict:
        async with self._client(credentials) as client:
            resp = await client.post(
                "/inventory_levels/set.json",
                json={
                    "location_id": setting.location_id,
                    "inventory_item_id": setting.inventory_item_id,
                    "available": setting.available,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_locations(self, credentials: dict) -> list[dict]:
        async with self._client(credentials) as client:
            resp = await client.get("/locations.json")
            resp.raise_for_status()
            return resp.json().get("locations", [])

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    async def get_customers(
        self, credentials: dict, limit: int = 250
    ) -> list[dict]:
        customers: list[dict] = []
        async with self._client(credentials) as client:
            async for page in self._paginate(
                client, "/customers.json", {"limit": limit}, "customers"
            ):
                customers.extend(page)
        return customers

    async def get_customer(self, credentials: dict, customer_id: str) -> Optional[dict]:
        async with self._client(credentials) as client:
            resp = await client.get(f"/customers/{customer_id}.json")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("customer")

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_shop_info(self, credentials: dict) -> dict:
        async with self._client(credentials) as client:
            resp = await client.get("/shop.json")
            resp.raise_for_status()
            return resp.json().get("shop", {})

    # ------------------------------------------------------------------
    # Legacy / autobot support
    # ------------------------------------------------------------------

    async def get_purchased_products(self, user_data: Any, option: Any = None) -> Any:
        if not user_data:
            return []
        creds = {
            "shop_domain": user_data.get("shop_domain", ""),
            "access_token": user_data.get("access_token", ""),
        }
        since_hours = (option or {}).get("since_hours", 24) if isinstance(option, dict) else 24
        return await self.get_orders(creds, OrderListOptions(since_hours=since_hours))

    async def _get_all_products_legacy(self, user_data: Any) -> Any:
        """Bridge-compatible wrapper. Delegates to the typed get_all_products."""
        if not user_data:
            return []
        creds = {
            "shop_domain": user_data.get("shop_domain", ""),
            "access_token": user_data.get("access_token", ""),
        }
        from schemas.commerce import ProductListOptions
        return await self.get_all_products(creds, ProductListOptions())

    async def start_autobot(self, user_data: Any, option: Any = None) -> None:
        """Shopify autobot: check new orders and trigger customer support."""
        if not user_data:
            return
        try:
            creds = {
                "shop_domain": user_data.get("shop_domain", ""),
                "access_token": user_data.get("access_token", ""),
            }
            orders = await self.get_orders(creds, OrderListOptions(since_hours=1))
            BackLog.info(instance=self, message=f"Shopify autobot: {len(orders)} new orders")
        except Exception as e:
            BackLog.exception(instance=self, message=f"Shopify autobot error: {e}")
