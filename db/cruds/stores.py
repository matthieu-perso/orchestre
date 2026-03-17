"""
Store CRUD operations.

A "store" is one connected account: one Shopify shop or one Amazon seller+marketplace.
The identifier_name (e.g. "mystore.myshopify.com") is the human-readable key used
everywhere in credentials + the bridge. The DB row gives us a stable UUID and lets us
resolve user_id from a webhook's shop_domain.
"""
import uuid
from typing import Optional

from sqlalchemy import select

from db.models.commerce import Store
from db.postgres import db_session


async def get_or_create_store(
    user_id: str,
    provider: str,
    identifier: str,
    *,
    shop_domain: Optional[str] = None,
    marketplace_id: Optional[str] = None,
    currency: str = "USD",
) -> Store:
    """
    Upsert a store row. Called after OAuth completes.
    Returns the Store row (existing or newly created).
    """
    async with db_session() as session:
        stmt = select(Store).where(
            Store.user_id == user_id,
            Store.provider == provider,
            Store.identifier == identifier,
        )
        result = await session.execute(stmt)
        store = result.scalar_one_or_none()

        if store:
            # Update metadata if it changed
            if shop_domain and store.shop_domain != shop_domain:
                store.shop_domain = shop_domain
            if marketplace_id and store.marketplace_id != marketplace_id:
                store.marketplace_id = marketplace_id
            return store

        store = Store(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=provider,
            identifier=identifier,
            shop_domain=shop_domain or identifier,
            marketplace_id=marketplace_id,
            currency=currency,
        )
        session.add(store)
        return store


async def get_store(user_id: str, provider: str, identifier: str) -> Optional[Store]:
    async with db_session() as session:
        stmt = select(Store).where(
            Store.user_id == user_id,
            Store.provider == provider,
            Store.identifier == identifier,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_store_by_id(store_id: uuid.UUID) -> Optional[Store]:
    async with db_session() as session:
        result = await session.execute(select(Store).where(Store.id == store_id))
        return result.scalar_one_or_none()


async def get_store_by_domain(shop_domain: str) -> Optional[Store]:
    """Used by webhook receiver to resolve user_id from Shopify shop domain."""
    async with db_session() as session:
        result = await session.execute(
            select(Store).where(Store.shop_domain == shop_domain, Store.is_active == True)
        )
        return result.scalar_one_or_none()


async def get_stores_for_user(user_id: str) -> list[Store]:
    """List all stores connected by this user."""
    async with db_session() as session:
        result = await session.execute(
            select(Store).where(Store.user_id == user_id, Store.is_active == True)
        )
        return list(result.scalars().all())


async def deactivate_store(user_id: str, provider: str, identifier: str) -> bool:
    async with db_session() as session:
        stmt = select(Store).where(
            Store.user_id == user_id,
            Store.provider == provider,
            Store.identifier == identifier,
        )
        result = await session.execute(stmt)
        store = result.scalar_one_or_none()
        if store:
            store.is_active = False
            return True
        return False
