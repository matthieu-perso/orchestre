"""
BaseProvider - stateless, typed abstract interface for all commerce connectors.

Key design principles vs. old version:
- NO mutable instance state (no self.access_token, self.sync_time, etc.)
- All credentials passed explicitly per call
- Typed parameters using Pydantic schemas
- Async-first
"""
from abc import ABCMeta, abstractmethod
from typing import Any, Optional

from simple_classproperty import ClasspropertyMeta, classproperty
from starlette.requests import Request

from schemas.commerce import (
    FulfillmentRequest,
    InventoryAdjustment,
    InventoryLevel,
    InventorySet,
    OrderListOptions,
    OrderSchema,
    ProductListOptions,
    ProductSchema,
    PriceUpdate,
    RefundRequest,
)


class BaseProviderMeta(ABCMeta, ClasspropertyMeta):
    pass


class BaseProvider(metaclass=BaseProviderMeta):
    """
    Abstract base for all provider plugins.
    Instances are stateless - credentials come from the caller each time.
    """

    def __init__(self) -> None:
        self.user_id: str = ""
        self.identifier_name: str = ""

    def set_base_info(self, user_id: str, identifier_name: str) -> None:
        self.user_id = user_id
        self.identifier_name = identifier_name

    def get_provider_info(self) -> dict:
        return {
            "provider": self.plugin_name.lower(),
            "provider_description": "Base Provider",
            "provider_icon_url": "",
        }

    # ------------------------------------------------------------------
    # Auth / OAuth
    # ------------------------------------------------------------------

    async def link_provider(self, redirect_url: str, request: Request) -> Any:
        raise NotImplementedError

    async def get_access_token(self, request: Request) -> str:
        raise NotImplementedError

    async def get_access_token_from_refresh_token(self, refresh_token: str) -> str:
        raise NotImplementedError

    async def disconnect(self, request: Request) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Autobot loop (keep for backward compat with Gmail etc.)
    # ------------------------------------------------------------------

    async def start_autobot(self, user_data: Any, option: Any = None) -> None:
        raise NotImplementedError

    def update_provider_info(self, user_data: Any, option: Any = None) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def get_all_products(
        self,
        credentials: dict,
        options: Optional[ProductListOptions] = None,
    ) -> list[ProductSchema]:
        raise NotImplementedError

    async def get_product(
        self, credentials: dict, product_id: str
    ) -> Optional[ProductSchema]:
        raise NotImplementedError

    async def update_product_price(
        self, credentials: dict, updates: list[PriceUpdate]
    ) -> list[dict]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def get_orders(
        self,
        credentials: dict,
        options: Optional[OrderListOptions] = None,
    ) -> list[OrderSchema]:
        raise NotImplementedError

    async def get_order(
        self, credentials: dict, order_id: str
    ) -> Optional[OrderSchema]:
        raise NotImplementedError

    async def fulfill_order(
        self, credentials: dict, request: FulfillmentRequest
    ) -> dict:
        raise NotImplementedError

    async def cancel_order(
        self, credentials: dict, order_id: str, reason: Optional[str] = None
    ) -> dict:
        raise NotImplementedError

    async def refund_order(
        self, credentials: dict, request: RefundRequest
    ) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    async def get_inventory_levels(
        self, credentials: dict, location_ids: Optional[list[str]] = None
    ) -> list[InventoryLevel]:
        raise NotImplementedError

    async def adjust_inventory(
        self, credentials: dict, adjustment: InventoryAdjustment
    ) -> dict:
        raise NotImplementedError

    async def set_inventory(
        self, credentials: dict, setting: InventorySet
    ) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Pricing / Competitive
    # ------------------------------------------------------------------

    async def get_competitor_prices(
        self, credentials: dict, sku_or_asin: str
    ) -> list[dict]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Legacy helpers (kept for backward compat)
    # ------------------------------------------------------------------

    async def get_purchased_products(self, user_data: Any, option: Any = None) -> Any:
        raise NotImplementedError

    async def scrapy_all_chats(self, user_data: Any, option: Any = None) -> Any:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Messages (used by Gmail and similar)
    # ------------------------------------------------------------------

    def get_last_message(self, access_token: str, option: Any) -> Any:
        raise NotImplementedError

    def get_full_messages(self, access_token: str, of_what: str, option: Any) -> Any:
        raise NotImplementedError

    def get_messages(self, access_token: str, from_when: str, count: int, option: Any) -> Any:
        raise NotImplementedError

    def reply_to_message(self, access_token: str, to: str, message: str, option: Any) -> Any:
        raise NotImplementedError

    @classproperty
    def plugin_name(cls) -> str:
        return cls.__name__  # type: ignore
