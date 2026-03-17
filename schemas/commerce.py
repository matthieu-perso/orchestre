"""
Typed Pydantic v2 schemas for all commerce operations.
These replace the untyped `option: any` pattern everywhere.
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

class Address(BaseSchema):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    phone: Optional[str] = None


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class ProductVariantSchema(BaseSchema):
    external_id: str
    sku: Optional[str] = None
    title: str
    price: Decimal
    compare_at_price: Optional[Decimal] = None
    cost_per_item: Optional[Decimal] = None
    inventory_quantity: int = 0
    weight: Optional[Decimal] = None
    barcode: Optional[str] = None
    option1: Optional[str] = None
    option2: Optional[str] = None
    option3: Optional[str] = None


class ProductSchema(BaseSchema):
    external_id: str
    title: str
    description: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    variants: list[ProductVariantSchema] = Field(default_factory=list)
    is_active: bool = True


class ProductListOptions(BaseSchema):
    limit: int = 250
    page_info: Optional[str] = None
    product_type: Optional[str] = None
    vendor: Optional[str] = None
    updated_at_min: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class InventoryLevel(BaseSchema):
    inventory_item_id: str
    location_id: str
    location_name: Optional[str] = None
    available: int
    incoming: Optional[int] = None
    sku: Optional[str] = None


class InventoryAdjustment(BaseSchema):
    inventory_item_id: str
    location_id: str
    available_adjustment: int
    reason: Optional[str] = None


class InventorySet(BaseSchema):
    inventory_item_id: str
    location_id: str
    available: int
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class LineItem(BaseSchema):
    external_id: str
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    sku: Optional[str] = None
    title: str
    quantity: int
    price: Decimal
    total_discount: Decimal = Decimal("0")
    fulfillment_status: Optional[str] = None
    fulfillable_quantity: int = 0


class OrderSchema(BaseSchema):
    external_id: str
    order_number: Optional[str] = None
    status: str = "pending"
    fulfillment_status: Optional[str] = None
    total_price: Decimal
    subtotal_price: Optional[Decimal] = None
    total_tax: Optional[Decimal] = None
    total_discounts: Optional[Decimal] = None
    total_shipping: Optional[Decimal] = None
    currency: str = "USD"
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    shipping_address: Optional[Address] = None
    line_items: list[LineItem] = Field(default_factory=list)
    tags: Optional[str] = None
    note: Optional[str] = None
    source_channel: Optional[str] = None
    ordered_at: Optional[datetime] = None


class OrderListOptions(BaseSchema):
    limit: int = 250
    status: str = "any"
    fulfillment_status: Optional[str] = None
    since_hours: int = 24
    page_info: Optional[str] = None


class FulfillmentRequest(BaseSchema):
    order_id: str
    line_item_ids: Optional[list[str]] = None
    tracking_number: Optional[str] = None
    tracking_company: Optional[str] = None
    tracking_url: Optional[str] = None
    notify_customer: bool = True
    location_id: Optional[str] = None


class RefundRequest(BaseSchema):
    order_id: str
    line_items: Optional[list[dict]] = None
    shipping: Optional[dict] = None
    note: Optional[str] = None
    notify: bool = True
    restock: bool = True


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

class CustomerSchema(BaseSchema):
    external_id: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    total_orders: int = 0
    total_spent: Decimal = Decimal("0")
    tags: Optional[str] = None
    accepts_marketing: bool = False
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Pricing / Repricing
# ---------------------------------------------------------------------------

class CompetitorPrice(BaseSchema):
    seller_id: Optional[str] = None
    seller_name: Optional[str] = None
    price: Decimal
    condition: str = "New"
    is_buybox_winner: bool = False
    shipping_price: Decimal = Decimal("0")
    landed_price: Decimal = Decimal("0")


class PriceRecommendation(BaseSchema):
    product_id: str
    variant_id: Optional[str] = None
    sku: Optional[str] = None
    current_price: Decimal
    recommended_price: Decimal
    min_price: Decimal
    max_price: Decimal
    strategy: str
    reason: str
    competitor_prices: list[CompetitorPrice] = Field(default_factory=list)
    expected_margin_pct: Optional[Decimal] = None


class PriceUpdate(BaseSchema):
    variant_id: str
    price: Decimal
    compare_at_price: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Ads
# ---------------------------------------------------------------------------

class AdCampaignMetrics(BaseSchema):
    campaign_id: str
    campaign_name: str
    impressions: int = 0
    clicks: int = 0
    spend: Decimal = Decimal("0")
    sales: Decimal = Decimal("0")
    orders: int = 0
    acos: Optional[Decimal] = None
    roas: Optional[Decimal] = None
    ctr: Optional[Decimal] = None
    cvr: Optional[Decimal] = None
    cpc: Optional[Decimal] = None


class KeywordPerformance(BaseSchema):
    keyword_id: str
    keyword_text: str
    match_type: str
    ad_group_id: str
    campaign_id: str
    impressions: int = 0
    clicks: int = 0
    spend: Decimal = Decimal("0")
    sales: Decimal = Decimal("0")
    orders: int = 0
    acos: Optional[Decimal] = None
    bid: Optional[Decimal] = None
    recommended_bid: Optional[Decimal] = None


class BidUpdate(BaseSchema):
    keyword_id: str
    ad_group_id: str
    old_bid: Decimal
    new_bid: Decimal
    reason: str


class BudgetUpdate(BaseSchema):
    campaign_id: str
    old_budget: Decimal
    new_budget: Decimal
    reason: str


class AdOptimizationResult(BaseSchema):
    platform: str
    store_id: str
    bid_updates: list[BidUpdate] = Field(default_factory=list)
    budget_updates: list[BudgetUpdate] = Field(default_factory=list)
    keywords_added: list[dict] = Field(default_factory=list)
    keywords_paused: list[str] = Field(default_factory=list)
    negatives_added: list[dict] = Field(default_factory=list)
    total_actions: int = 0
    dry_run: bool = False
    ran_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Inventory / Restock
# ---------------------------------------------------------------------------

class RestockRecommendation(BaseSchema):
    sku: str
    product_title: str
    current_stock: int
    days_of_stock_remaining: float
    avg_daily_sales: float
    reorder_point: int
    recommended_order_qty: int
    estimated_cost: Optional[Decimal] = None
    urgency: str = "normal"  # normal | high | critical


# ---------------------------------------------------------------------------
# Customer support
# ---------------------------------------------------------------------------

class SupportMessage(BaseSchema):
    message_id: Optional[str] = None
    order_id: Optional[str] = None
    customer_email: Optional[str] = None
    subject: Optional[str] = None
    content: str
    channel: str = "email"
    sentiment: Optional[str] = None
    category: Optional[str] = None


class SupportResponse(BaseSchema):
    message_id: Optional[str] = None
    response_text: str
    category: str
    sentiment: str
    confidence: float
    should_escalate: bool = False
    escalation_reason: Optional[str] = None
    actions_taken: list[str] = Field(default_factory=list)
