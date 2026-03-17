"""
Commerce API endpoints.

Direct operations on Shopify/Amazon via the provider layer:
- Products: list, get, update price
- Orders: list, get, fulfill, cancel, refund
- Inventory: list levels, adjust, set
- Customers: list, get
"""
from typing import Optional

from fastapi import APIRouter, Depends

from core.utils.message import MessageErr, MessageOK
from db.cruds.users import get_user_data
from providers.bridge import bridge
from schemas.commerce import (
    FulfillmentRequest,
    InventoryAdjustment,
    InventorySet,
    OrderListOptions,
    ProductListOptions,
    PriceUpdate,
    RefundRequest,
)

from .users import User, get_current_user

router = APIRouter()


def _get_credentials(user_id: str, provider: str, identifier: str) -> dict:
    user_data = get_user_data(user_id) or {}
    return user_data.get(provider, {}).get(identifier, {})


def _provider(provider_name: str):
    return bridge.shared_provider_list.get(provider_name.lower())


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@router.get(
    "/products",
    summary="List all products",
    description="List all products for a connected store account.",
)
async def list_products(
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    limit: int = 250,
    product_type: Optional[str] = None,
    vendor: Optional[str] = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        options = ProductListOptions(limit=limit, product_type=product_type, vendor=vendor)
        result = await p.get_all_products(creds, options)
        return MessageOK(data={"products": [r.model_dump() for r in result]})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/products/{product_id}",
    summary="Get a single product",
)
async def get_product(
    product_id: str,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.get_product(creds, product_id)
        return MessageOK(data={"product": result.model_dump() if result else None})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/products/update_price",
    summary="Update product prices",
    description="Bulk update variant prices for a store.",
)
async def update_product_prices(
    updates: list[PriceUpdate],
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.update_product_price(creds, updates)
        return MessageOK(data={"updated": result})
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@router.get(
    "/orders",
    summary="List orders",
)
async def list_orders(
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    since_hours: int = 24,
    status: str = "any",
    fulfillment_status: Optional[str] = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        options = OrderListOptions(
            since_hours=since_hours,
            status=status,
            fulfillment_status=fulfillment_status,
        )
        result = await p.get_orders(creds, options)
        return MessageOK(data={"orders": [r.model_dump() for r in result]})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/orders/{order_id}",
    summary="Get a single order",
)
async def get_order(
    order_id: str,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.get_order(creds, order_id)
        return MessageOK(data={"order": result.model_dump() if result else None})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/orders/fulfill",
    summary="Fulfill an order",
)
async def fulfill_order(
    request: FulfillmentRequest,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.fulfill_order(creds, request)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/orders/cancel",
    summary="Cancel an order",
)
async def cancel_order(
    order_id: str,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    reason: Optional[str] = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.cancel_order(creds, order_id, reason)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/orders/refund",
    summary="Refund an order",
)
async def refund_order(
    request: RefundRequest,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.refund_order(creds, request)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@router.get(
    "/inventory",
    summary="List inventory levels",
)
async def list_inventory(
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    location_ids: Optional[str] = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        lids = location_ids.split(",") if location_ids else None
        result = await p.get_inventory_levels(creds, lids)
        return MessageOK(data={"inventory": [r.model_dump() for r in result]})
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/inventory/adjust",
    summary="Adjust inventory quantity",
)
async def adjust_inventory(
    adjustment: InventoryAdjustment,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.adjust_inventory(creds, adjustment)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/inventory/set",
    summary="Set inventory to exact quantity",
)
async def set_inventory(
    setting: InventorySet,
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p:
            return MessageErr(reason=f"Provider {provider_name} not found")
        result = await p.set_inventory(creds, setting)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Customers (Shopify)
# ---------------------------------------------------------------------------

@router.get(
    "/customers",
    summary="List customers",
)
async def list_customers(
    provider_name: str = "shopifyprovider",
    identifier_name: str = "",
    limit: int = 250,
    curr_user: User = Depends(get_current_user),
):
    try:
        creds = _get_credentials(curr_user["uid"], provider_name, identifier_name)
        p = _provider(provider_name)
        if not p or not hasattr(p, "get_customers"):
            return MessageErr(reason=f"Provider {provider_name} does not support customers")
        result = await p.get_customers(creds, limit=limit)
        return MessageOK(data={"customers": result})
    except Exception as e:
        return MessageErr(reason=str(e))
