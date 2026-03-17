"""
Amazon Selling Partner API (SP-API) provider.

Supports:
- LWA (Login With Amazon) OAuth2 token management
- Orders: getOrders, getOrder, getOrderItems
- Catalog: getCatalogItem, searchCatalogItems
- Listings: getListingsItem, putListingsItem, deleteListingsItem
- FBA Inventory: getInventorySummaries
- Pricing: getCompetitivePricing, getPricing, getItemOffers
- Fulfillment: createFulfillmentOrder, getFulfillmentOrder
- Reports: createReport, getReport, getReportDocument

Credentials dict expected:
  {
    "refresh_token": "Atzr|...",     # seller-specific LWA refresh token
    "seller_id": "AXXXXXXXXXX",
    "marketplace_id": "ATVPDKIKX0DER"  # US = ATVPDKIKX0DER, etc.
  }
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from starlette.requests import Request

from core.config import settings
from core.utils.log import BackLog
from providers.base import BaseProvider
from schemas.commerce import (
    CompetitorPrice,
    FulfillmentRequest,
    InventoryLevel,
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

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_BASE = settings.AMAZON_ENDPOINT

# Marketplace IDs
MARKETPLACE_IDS = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8",
    "UK": "A1F83G8C2ARO7P",
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYZZH",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9HS",
    "JP": "A1VC38T7YXB528",
    "AU": "A39IBJ37TRP1C6",
    "IN": "A21TJRUUN4KGV",
}


class AmazonProvider(BaseProvider):
    """Amazon SP-API connector."""

    def get_provider_info(self) -> dict:
        return {
            "provider": self.plugin_name.lower(),
            "short_name": "Amazon",
            "provider_description": "Amazon Marketplace",
            "provider_icon_url": "/amazon.svg",
        }

    # ------------------------------------------------------------------
    # LWA Auth helpers
    # ------------------------------------------------------------------

    async def _get_lwa_token(self, refresh_token: str) -> tuple[str, int]:
        """Fetch a fresh LWA access token. Returns (token, expires_in_seconds)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.AMAZON_CLIENT_ID,
                    "client_secret": settings.AMAZON_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["access_token"], int(data.get("expires_in", 3600))

    def _sp_client(self, access_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=SP_API_BASE,
            headers={
                "x-amz-access-token": access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def _sp_request(self, credentials: dict, method: str, path: str, **kwargs) -> httpx.Response:
        """
        Make a signed SP-API request with automatic token-expiry retry.
        On 401: invalidates the cache, fetches a fresh token, retries once.
        """
        from core.auth.token_cache import token_cache

        seller_id = credentials.get("seller_id", "")
        token = await self._credentials_to_token(credentials)

        async with self._sp_client(token) as client:
            resp = await client.request(method, path, **kwargs)

        if resp.status_code == 401:
            # Token may have just expired — bust the cache and retry once
            await token_cache.delete("amazon_lwa", seller_id)
            fresh_token = await self._credentials_to_token(credentials)
            async with self._sp_client(fresh_token) as client:
                resp = await client.request(method, path, **kwargs)

        return resp

    async def _credentials_to_token(self, credentials: dict) -> str:
        """
        Return a valid access token for these credentials.
        - If an unexpired token is in Redis: return it immediately (no network call).
        - Otherwise: refresh via LWA, cache the new token, return it.
        The refresh_token itself never expires and stays in Firebase forever.
        """
        from core.auth.token_cache import token_cache

        refresh_token = credentials.get("refresh_token", "")
        seller_id = credentials.get("seller_id", refresh_token[:16])

        async def _do_refresh():
            token, ttl = await self._get_lwa_token(refresh_token)
            return token, ttl

        return await token_cache.get_or_refresh(
            provider="amazon_lwa",
            account_key=seller_id,
            refresh_fn=_do_refresh,
        )

    # ------------------------------------------------------------------
    # Auth / OAuth
    # ------------------------------------------------------------------

    async def link_provider(self, redirect_url: str, request: Request) -> Any:
        """Generate Amazon SP-API OAuth URL for seller authorization."""
        from urllib.parse import urlencode
        from starlette.responses import RedirectResponse

        application_id = settings.AMAZON_CLIENT_ID
        state = hashlib.sha256(redirect_url.encode()).hexdigest()[:16]
        request.session["amazon_state"] = state
        request.session["amazon_redirect"] = redirect_url

        params = {
            "application_id": application_id,
            "state": state,
            "redirect_uri": f"{settings.WEBHOOK_BASE_URL}/providers/amazon_auth",
            "version": "beta",
        }
        return {"redirect_url": f"https://sellercentral.amazon.com/apps/authorize/consent?{urlencode(params)}"}

    async def get_access_token(self, request: Request) -> Any:
        """Exchange spapi_oauth_code for LWA tokens."""
        from starlette.responses import RedirectResponse

        code = request.query_params.get("spapi_oauth_code")
        selling_partner_id = request.query_params.get("selling_partner_id")
        state = request.query_params.get("state")

        if state != request.session.get("amazon_state"):
            return {"error": "Invalid state"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.AMAZON_CLIENT_ID,
                    "client_secret": settings.AMAZON_CLIENT_SECRET,
                    "redirect_uri": f"{settings.WEBHOOK_BASE_URL}/providers/amazon_auth",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        user_id = request.session.get("user_id", "")

        credentials = {
            "seller_id": selling_partner_id,
            "refresh_token": data["refresh_token"],
            # access_token intentionally NOT saved — it's short-lived (1h).
            # The automation fetches + caches it from the refresh_token automatically.
            "marketplace_id": MARKETPLACE_IDS["US"],
        }

        if user_id and selling_partner_id:
            # Auto-save credentials to Firebase
            try:
                import json
                from db.cruds.users import update_user
                from db.schemas.users import UsersSchema
                update_user(
                    user=UsersSchema(id=user_id, email=""),
                    provider_name="amazonprovider",
                    key=selling_partner_id,
                    content=json.dumps(credentials),
                )
            except Exception as e:
                logger.warning("Auto-save Amazon credentials failed (non-fatal): %s", e)

            # Register store row in Postgres
            try:
                from db.cruds.stores import get_or_create_store
                await get_or_create_store(
                    user_id=user_id,
                    provider="amazonprovider",
                    identifier=selling_partner_id,
                    marketplace_id=MARKETPLACE_IDS["US"],
                )
            except Exception as e:
                logger.warning("Store registration failed (non-fatal): %s", e)

        redirect_url = request.session.get("amazon_redirect", "/")
        return_params = {
            "provider": "amazonprovider",
            "identifier_name": selling_partner_id,
            "seller_id": selling_partner_id,
            "connected": "true",
        }
        return RedirectResponse(url=redirect_url + "?" + urlencode(return_params))

    async def disconnect(self, request: Request) -> None:
        pass

    # ------------------------------------------------------------------
    # Products / Catalog
    # ------------------------------------------------------------------

    async def get_catalog_item(self, credentials: dict, asin: str) -> Optional[dict]:
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])
        async with self._sp_client(token) as client:
            resp = await client.get(
                f"/catalog/2022-04-01/items/{asin}",
                params={
                    "marketplaceIds": marketplace_id,
                    "includedData": "attributes,dimensions,images,productTypes,summaries",
                },
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def search_catalog(
        self, credentials: dict, keywords: str, page_size: int = 20
    ) -> list[dict]:
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])
        async with self._sp_client(token) as client:
            resp = await client.get(
                "/catalog/2022-04-01/items",
                params={
                    "keywords": keywords,
                    "marketplaceIds": marketplace_id,
                    "pageSize": page_size,
                    "includedData": "summaries",
                },
            )
            resp.raise_for_status()
            return resp.json().get("items", [])

    async def get_all_products(  # type: ignore[override]
        self,
        credentials: dict,
        options: Optional[ProductListOptions] = None,
    ) -> list[ProductSchema]:
        """Get all listings for the seller."""
        token = await self._credentials_to_token(credentials)
        seller_id = credentials.get("seller_id", "")
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])

        all_listings: list[ProductSchema] = []
        next_token: Optional[str] = None

        async with self._sp_client(token) as client:
            while True:
                params: dict = {
                    "sellerId": seller_id,
                    "marketplaceIds": marketplace_id,
                    "pageSize": 10,
                }
                if next_token:
                    params["pageToken"] = next_token

                resp = await client.get("/listings/2021-08-01/items", params=params)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("items", []):
                    attrs = item.get("attributes", {})
                    price_val = attrs.get("purchasable_offer", [{}])[0].get("our_price", [{}])[0].get("schedule", [{}])[0].get("value_with_tax", "0") if attrs.get("purchasable_offer") else "0"

                    variant = ProductVariantSchema(
                        external_id=item.get("listingId", ""),
                        sku=item.get("sku", ""),
                        title=(attrs.get("item_name", [{}])[0].get("value", "")) if attrs.get("item_name") else item.get("sku", ""),
                        price=Decimal(str(price_val)),
                    )
                    all_listings.append(ProductSchema(
                        external_id=item.get("sku", ""),
                        title=variant.title,
                        asin=item.get("asin"),  # type: ignore[call-arg]
                        variants=[variant],
                    ))

                next_token = data.get("pagination", {}).get("nextToken")
                if not next_token:
                    break

        return all_listings

    async def update_listing_price(
        self, credentials: dict, sku: str, price: Decimal, marketplace_id: Optional[str] = None
    ) -> dict:
        token = await self._credentials_to_token(credentials)
        seller_id = credentials.get("seller_id", "")
        mp_id = marketplace_id or credentials.get("marketplace_id", MARKETPLACE_IDS["US"])

        payload = {
            "productType": "PRODUCT",
            "patches": [
                {
                    "op": "replace",
                    "path": "/attributes/purchasable_offer",
                    "value": [
                        {
                            "marketplace_id": mp_id,
                            "currency": "USD",
                            "our_price": [{"schedule": [{"value_with_tax": float(price)}]}],
                        }
                    ],
                }
            ],
        }

        async with self._sp_client(token) as client:
            resp = await client.patch(
                f"/listings/2021-08-01/items/{seller_id}/{sku}",
                params={"marketplaceIds": mp_id},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _parse_sp_order(self, raw: dict) -> OrderSchema:
        ordered_at = None
        if raw.get("PurchaseDate"):
            try:
                ordered_at = datetime.fromisoformat(raw["PurchaseDate"].replace("Z", "+00:00"))
            except Exception:
                pass

        total = raw.get("OrderTotal", {})

        return OrderSchema(
            external_id=raw.get("AmazonOrderId", ""),
            order_number=raw.get("AmazonOrderId"),
            status=raw.get("OrderStatus", "pending").lower(),
            fulfillment_status=raw.get("FulfillmentChannel"),
            total_price=Decimal(str(total.get("Amount", "0"))) if total else Decimal("0"),
            currency=total.get("CurrencyCode", "USD") if total else "USD",
            customer_email=raw.get("BuyerInfo", {}).get("BuyerEmail"),
            customer_name=raw.get("BuyerInfo", {}).get("BuyerName"),
            source_channel=raw.get("FulfillmentChannel"),
            ordered_at=ordered_at,
        )

    async def get_orders(
        self,
        credentials: dict,
        options: Optional[OrderListOptions] = None,
    ) -> list[OrderSchema]:
        options = options or OrderListOptions()
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])
        since = datetime.now(timezone.utc) - timedelta(hours=options.since_hours)

        all_orders: list[OrderSchema] = []
        next_token: Optional[str] = None

        async with self._sp_client(token) as client:
            while True:
                params: dict = {
                    "MarketplaceIds": marketplace_id,
                    "CreatedAfter": since.isoformat(),
                    "MaxResultsPerPage": 100,
                }
                if options.fulfillment_status:
                    params["FulfillmentChannels"] = options.fulfillment_status
                if next_token:
                    params["NextToken"] = next_token

                resp = await client.get("/orders/v0/orders", params=params)
                resp.raise_for_status()
                data = resp.json().get("payload", {})

                for raw in data.get("Orders", []):
                    all_orders.append(self._parse_sp_order(raw))

                next_token = data.get("NextToken")
                if not next_token:
                    break

        return all_orders

    async def get_order(self, credentials: dict, order_id: str) -> Optional[OrderSchema]:
        token = await self._credentials_to_token(credentials)
        async with self._sp_client(token) as client:
            resp = await client.get(f"/orders/v0/orders/{order_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            order = resp.json().get("payload", {})

            # Fetch line items
            items_resp = await client.get(f"/orders/v0/orders/{order_id}/orderItems")
            items_resp.raise_for_status()
            items_data = items_resp.json().get("payload", {}).get("OrderItems", [])

            line_items = [
                LineItem(
                    external_id=str(item.get("OrderItemId", "")),
                    sku=item.get("SellerSKU"),
                    title=item.get("Title", ""),
                    quantity=item.get("QuantityOrdered", 0),
                    price=Decimal(str(item.get("ItemPrice", {}).get("Amount", "0"))),
                )
                for item in items_data
            ]
            parsed = self._parse_sp_order(order)
            parsed.line_items = line_items
            return parsed

    async def get_purchased_products(self, user_data: Any, option: Any = None) -> Any:
        if not user_data:
            return []
        since_hours = (option or {}).get("since_hours", 24) if isinstance(option, dict) else 24
        return await self.get_orders(user_data, OrderListOptions(since_hours=since_hours))

    # ------------------------------------------------------------------
    # FBA Inventory
    # ------------------------------------------------------------------

    async def get_fba_inventory(
        self, credentials: dict, granularity: str = "Marketplace"
    ) -> list[dict]:
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])

        summaries: list[dict] = []
        next_token: Optional[str] = None

        async with self._sp_client(token) as client:
            while True:
                params: dict = {
                    "granularityType": granularity,
                    "granularityId": marketplace_id,
                    "marketplaceIds": marketplace_id,
                }
                if next_token:
                    params["nextToken"] = next_token

                resp = await client.get(
                    "/fba/inventory/v1/summaries",
                    params=params,
                )
                resp.raise_for_status()
                payload = resp.json().get("payload", {})
                summaries.extend(payload.get("inventorySummaries", []))

                next_token = payload.get("pagination", {}).get("nextToken")
                if not next_token:
                    break

        return summaries

    async def get_inventory_levels(
        self, credentials: dict, location_ids: Optional[list[str]] = None
    ) -> list[InventoryLevel]:
        summaries = await self.get_fba_inventory(credentials)
        return [
            InventoryLevel(
                inventory_item_id=s.get("fnSku", s.get("sellerSku", "")),
                location_id="FBA",
                location_name="Fulfillment by Amazon",
                available=s.get("inventoryDetails", {}).get("fulfillableQuantity", 0),
                incoming=s.get("inventoryDetails", {}).get("inboundWorkingQuantity", 0),
                sku=s.get("sellerSku"),
            )
            for s in summaries
        ]

    # ------------------------------------------------------------------
    # Pricing / Competitive
    # ------------------------------------------------------------------

    async def get_competitor_prices(
        self, credentials: dict, sku_or_asin: str
    ) -> list[CompetitorPrice]:
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])

        async with self._sp_client(token) as client:
            resp = await client.get(
                "/products/pricing/v0/competitivePrice",
                params={
                    "MarketplaceId": marketplace_id,
                    "Asins": sku_or_asin,
                    "ItemType": "Asin",
                },
            )
            resp.raise_for_status()
            payload = resp.json().get("payload", [])

        prices: list[CompetitorPrice] = []
        for item in payload:
            for cp in item.get("Product", {}).get("CompetitivePricing", {}).get("CompetitivePrices", []):
                price_val = Decimal(str(cp.get("Price", {}).get("LandedPrice", {}).get("Amount", "0")))
                prices.append(CompetitorPrice(
                    price=price_val,
                    landed_price=price_val,
                    is_buybox_winner=cp.get("belongsToBuyBox", False),
                    condition=cp.get("condition", "New"),
                ))

        return prices

    async def get_buybox_price(
        self, credentials: dict, asin: str
    ) -> Optional[Decimal]:
        prices = await self.get_competitor_prices(credentials, asin)
        buybox = next((p for p in prices if p.is_buybox_winner), None)
        if buybox:
            return buybox.price
        return min((p.price for p in prices), default=None)

    async def get_pricing(
        self, credentials: dict, skus: list[str]
    ) -> list[dict]:
        token = await self._credentials_to_token(credentials)
        marketplace_id = credentials.get("marketplace_id", MARKETPLACE_IDS["US"])
        seller_id = credentials.get("seller_id", "")

        async with self._sp_client(token) as client:
            resp = await client.get(
                "/products/pricing/v0/price",
                params={
                    "MarketplaceId": marketplace_id,
                    "Skus": ",".join(skus),
                    "ItemType": "Sku",
                    "SellerId": seller_id,
                },
            )
            resp.raise_for_status()
            return resp.json().get("payload", [])

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def create_report(
        self, credentials: dict, report_type: str, marketplace_ids: Optional[list[str]] = None
    ) -> dict:
        """
        Common report types:
          GET_FLAT_FILE_OPEN_LISTINGS_DATA
          GET_MERCHANT_LISTINGS_ALL_DATA
          GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL
          GET_FBA_MYI_ALL_INVENTORY_DATA
          GET_SELLER_FEEDBACK_DATA
          GET_V2_SELLER_PERFORMANCE_REPORT
        """
        token = await self._credentials_to_token(credentials)
        mp_ids = marketplace_ids or [credentials.get("marketplace_id", MARKETPLACE_IDS["US"])]

        async with self._sp_client(token) as client:
            resp = await client.post(
                "/reports/2021-06-30/reports",
                json={"reportType": report_type, "marketplaceIds": mp_ids},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_report_status(self, credentials: dict, report_id: str) -> dict:
        token = await self._credentials_to_token(credentials)
        async with self._sp_client(token) as client:
            resp = await client.get(f"/reports/2021-06-30/reports/{report_id}")
            resp.raise_for_status()
            return resp.json()

    async def get_report_document(self, credentials: dict, document_id: str) -> bytes:
        token = await self._credentials_to_token(credentials)
        async with self._sp_client(token) as client:
            meta_resp = await client.get(
                f"/reports/2021-06-30/documents/{document_id}"
            )
            meta_resp.raise_for_status()
            doc_url = meta_resp.json()["url"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(doc_url)
            resp.raise_for_status()
            return resp.content

    # ------------------------------------------------------------------
    # Fulfillment (MFN)
    # ------------------------------------------------------------------

    async def confirm_shipment(
        self, credentials: dict, order_id: str, tracking: dict
    ) -> dict:
        token = await self._credentials_to_token(credentials)
        async with self._sp_client(token) as client:
            resp = await client.post(
                f"/orders/v0/orders/{order_id}/shipment",
                json={
                    "marketplaceId": credentials.get("marketplace_id", MARKETPLACE_IDS["US"]),
                    "packageDetail": {
                        "packageReferenceId": "1",
                        "carrierCode": tracking.get("carrier_code", ""),
                        "trackingNumber": tracking.get("tracking_number", ""),
                        "shipDate": datetime.now(timezone.utc).isoformat(),
                        "orderItems": tracking.get("order_items", []),
                    },
                },
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Autobot
    # ------------------------------------------------------------------

    async def start_autobot(self, user_data: Any, option: Any = None) -> None:
        if not user_data:
            return
        try:
            orders = await self.get_orders(user_data, OrderListOptions(since_hours=1))
            BackLog.info(instance=self, message=f"Amazon autobot: {len(orders)} new orders")
        except Exception as e:
            BackLog.exception(instance=self, message=f"Amazon autobot error: {e}")

    async def _get_all_products_legacy(self, user_data: Any) -> Any:
        """Bridge-compatible wrapper. Returns FBA inventory summaries."""
        if not user_data:
            return []
        return await self.get_fba_inventory(user_data)
