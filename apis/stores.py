"""
Stores API.

A "store" is a connected commerce account (one Shopify shop or one Amazon seller).
One user can connect as many stores as they want — the identifier_name is the key.

Endpoints:
  GET  /stores              → list all connected stores for current user
  POST /stores/register     → manually register a store (if OAuth already done)
  DELETE /stores/{identifier} → deactivate a store
"""
from typing import Optional

from fastapi import APIRouter, Depends

from core.utils.message import MessageErr, MessageOK
from db.cruds.stores import (
    deactivate_store,
    get_or_create_store,
    get_stores_for_user,
)

from .users import User, get_current_user

router = APIRouter()


@router.get(
    "",
    summary="List all connected stores",
    description="Returns all Shopify and Amazon stores connected to your account, "
                "with their identifier_name (the value you use in all other API calls).",
)
async def list_stores(curr_user: User = Depends(get_current_user)):
    try:
        stores = await get_stores_for_user(curr_user["uid"])
        return MessageOK(data={
            "stores": [
                {
                    "store_id": str(s.id),
                    "identifier_name": s.identifier,   # use this in all commerce + optimize calls
                    "provider": s.provider,
                    "shop_domain": s.shop_domain,
                    "marketplace_id": s.marketplace_id,
                    "currency": s.currency,
                    "is_active": s.is_active,
                    "created_at": s.created_at.isoformat(),
                }
                for s in stores
            ]
        })
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/register",
    summary="Register a store",
    description="Manually register a store after completing OAuth outside the app. "
                "The identifier_name for Shopify is the shop domain (e.g. mystore.myshopify.com). "
                "For Amazon it is the seller_id.",
)
async def register_store(
    provider: str,
    identifier_name: str,
    shop_domain: Optional[str] = None,
    marketplace_id: Optional[str] = None,
    currency: str = "USD",
    curr_user: User = Depends(get_current_user),
):
    try:
        store = await get_or_create_store(
            user_id=curr_user["uid"],
            provider=provider,
            identifier=identifier_name,
            shop_domain=shop_domain or identifier_name,
            marketplace_id=marketplace_id,
            currency=currency,
        )
        return MessageOK(data={
            "store_id": str(store.id),
            "identifier_name": store.identifier,
            "provider": store.provider,
            "message": "Store registered. Use identifier_name in all subsequent API calls.",
        })
    except Exception as e:
        return MessageErr(reason=str(e))


@router.delete(
    "/{identifier_name}",
    summary="Disconnect a store",
)
async def disconnect_store(
    identifier_name: str,
    provider: str = "shopifyprovider",
    curr_user: User = Depends(get_current_user),
):
    try:
        ok = await deactivate_store(curr_user["uid"], provider, identifier_name)
        if ok:
            return MessageOK(data={"message": f"Store {identifier_name} deactivated"})
        return MessageErr(reason="Store not found")
    except Exception as e:
        return MessageErr(reason=str(e))
