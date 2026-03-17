"""
Commerce data models - all transactional e-commerce data lives here (Postgres).
User auth remains in Firebase.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.postgres import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def now_utc() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OrderStatus(str, PyEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class FulfillmentStatus(str, PyEnum):
    UNFULFILLED = "unfulfilled"
    PARTIAL = "partial"
    FULFILLED = "fulfilled"
    RESTOCKED = "restocked"


class AdPlatform(str, PyEnum):
    AMAZON = "amazon"
    META = "meta"
    GOOGLE = "google"


class AdObjective(str, PyEnum):
    AWARENESS = "awareness"
    CONSIDERATION = "consideration"
    CONVERSION = "conversion"


class RepricingStrategy(str, PyEnum):
    COMPETITIVE_LOWEST = "competitive_lowest"
    COMPETITIVE_BUYBOX = "competitive_buybox"
    RULE_BASED = "rule_based"
    AI_OPTIMIZED = "ai_optimized"


# ---------------------------------------------------------------------------
# Store (per connected account)
# ---------------------------------------------------------------------------

class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str] = mapped_column(String(256), nullable=False)
    shop_domain: Mapped[Optional[str]] = mapped_column(String(256))
    marketplace_id: Mapped[Optional[str]] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "identifier", name="uq_store"),
    )

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="store")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="store")
    campaigns: Mapped[list["AdCampaign"]] = relationship("AdCampaign", back_populates="store")
    fulfillment_rules: Mapped[list["FulfillmentRule"]] = relationship(
        "FulfillmentRule", back_populates="store"
    )


# ---------------------------------------------------------------------------
# Products & Inventory
# ---------------------------------------------------------------------------

class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    asin: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    vendor: Mapped[Optional[str]] = mapped_column(String(256))
    product_type: Mapped[Optional[str]] = mapped_column(String(128))
    tags: Mapped[Optional[list]] = mapped_column(JSON)
    images: Mapped[Optional[list]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship("Store", back_populates="products")
    variants: Mapped[list["ProductVariant"]] = relationship("ProductVariant", back_populates="product")
    inventory_items: Mapped[list["InventoryItem"]] = relationship("InventoryItem", back_populates="product")
    price_history: Mapped[list["PriceHistory"]] = relationship("PriceHistory", back_populates="product")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", name="uq_product_store"),
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    compare_at_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    cost_per_item: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    barcode: Mapped[Optional[str]] = mapped_column(String(128))
    option1: Mapped[Optional[str]] = mapped_column(String(256))
    option2: Mapped[Optional[str]] = mapped_column(String(256))
    option3: Mapped[Optional[str]] = mapped_column(String(256))
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product"] = relationship("Product", back_populates="variants")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("products.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    location_id: Mapped[Optional[str]] = mapped_column(String(128))
    location_name: Mapped[Optional[str]] = mapped_column(String(256))
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0)
    quantity_incoming: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[Optional[int]] = mapped_column(Integer)
    reorder_quantity: Mapped[Optional[int]] = mapped_column(Integer)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped[Optional["Product"]] = relationship("Product", back_populates="inventory_items")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", "location_id", name="uq_inventory"),
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    order_number: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.PENDING
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        Enum(FulfillmentStatus), default=FulfillmentStatus.UNFULFILLED
    )
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_tax: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_discounts: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_shipping: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    customer_id: Mapped[Optional[str]] = mapped_column(String(128))
    customer_email: Mapped[Optional[str]] = mapped_column(String(256))
    customer_name: Mapped[Optional[str]] = mapped_column(String(256))
    shipping_address: Mapped[Optional[dict]] = mapped_column(JSON)
    line_items: Mapped[Optional[list]] = mapped_column(JSON)
    tags: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)
    source_channel: Mapped[Optional[str]] = mapped_column(String(64))
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    ordered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship("Store", back_populates="orders")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", name="uq_order_store"),
    )


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    variant_external_id: Mapped[Optional[str]] = mapped_column(String(128))
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    strategy: Mapped[Optional[str]] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    competitor_prices: Mapped[Optional[list]] = mapped_column(JSON)
    applied_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = now_utc()

    product: Mapped["Product"] = relationship("Product", back_populates="price_history")


class RepricingRule(Base):
    __tablename__ = "repricing_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    strategy: Mapped[RepricingStrategy] = mapped_column(
        Enum(RepricingStrategy), default=RepricingStrategy.COMPETITIVE_BUYBOX
    )
    min_price_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=0.9)
    max_price_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=1.2)
    target_margin_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    undercut_by_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    applies_to_skus: Mapped[Optional[list]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = now_utc()


# ---------------------------------------------------------------------------
# Advertising
# ---------------------------------------------------------------------------

class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    platform: Mapped[AdPlatform] = mapped_column(Enum(AdPlatform), nullable=False)
    external_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    objective: Mapped[Optional[AdObjective]] = mapped_column(Enum(AdObjective))
    status: Mapped[str] = mapped_column(String(64), default="ENABLED")
    daily_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    targeting_type: Mapped[Optional[str]] = mapped_column(String(64))
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship("Store", back_populates="campaigns")
    metrics: Mapped[list["AdMetrics"]] = relationship("AdMetrics", back_populates="campaign")


class AdMetrics(Base):
    __tablename__ = "ad_metrics"

    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ad_campaigns.id"), index=True)
    ad_group_id: Mapped[Optional[str]] = mapped_column(String(128))
    keyword_id: Mapped[Optional[str]] = mapped_column(String(128))
    keyword_text: Mapped[Optional[str]] = mapped_column(String(512))
    match_type: Mapped[Optional[str]] = mapped_column(String(32))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    sales: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    acos: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    roas: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    cvr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = now_utc()

    campaign: Mapped["AdCampaign"] = relationship("AdCampaign", back_populates="metrics")


class AdOptimizationLog(Base):
    __tablename__ = "ad_optimization_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    platform: Mapped[AdPlatform] = mapped_column(Enum(AdPlatform), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(128))
    old_value: Mapped[Optional[dict]] = mapped_column(JSON)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = now_utc()


# ---------------------------------------------------------------------------
# Automation jobs / audit trail
# ---------------------------------------------------------------------------

class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_utc()


# ---------------------------------------------------------------------------
# Fulfillment rules
# ---------------------------------------------------------------------------

class FulfillmentActionType(str, PyEnum):
    FULFILL = "fulfill"      # auto-fulfill with carrier
    SKIP = "skip"            # skip (FBA, digital, dropship)
    FLAG = "flag"            # alert operator, do not auto-fulfill
    HOLD = "hold"            # hold for manual review


class FulfillmentRule(Base):
    """
    Per-store rule that determines how an order should be fulfilled.

    Rules are evaluated in ascending priority order.
    The first matching rule wins. If no rule matches, the order is flagged.

    conditions (JSON):
      payment_statuses: list[str]   e.g. ["paid"]
      fulfillment_types: list[str]  e.g. ["fba", "manual", "digital"]
      min_order_value: float | null
      max_order_value: float | null
      product_tags: list[str]       order must contain a product with ALL these tags
      country_codes: list[str]      e.g. ["US", "CA"] — empty = any country
      exclude_country_codes: list[str]

    action_config (JSON):
      carrier_strategy: "cheapest" | "fastest" | "overnight" | "balanced"
      preferred_carriers: list[str]  e.g. ["USPS", "UPS"]
      notify_customer: bool
      label_format: "PDF" | "PNG" | "ZPL"
      package_weight_oz: float | null  (override per-rule if all items ship same box)
    """
    __tablename__ = "fulfillment_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    action_type: Mapped[FulfillmentActionType] = mapped_column(
        Enum(FulfillmentActionType), nullable=False, default=FulfillmentActionType.FULFILL
    )
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    action_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = now_utc()
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship("Store", back_populates="fulfillment_rules")


class FulfillmentLog(Base):
    """
    Audit trail of every fulfillment action taken by the engine.
    Successful, failed, and skipped — all logged here.
    """
    __tablename__ = "fulfillment_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(128), index=True)
    rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("fulfillment_rules.id"))
    action_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))   # success | failed | skipped
    tracking_number: Mapped[Optional[str]] = mapped_column(String(128))
    label_url: Mapped[Optional[str]] = mapped_column(Text)
    carrier: Mapped[Optional[str]] = mapped_column(String(64))
    service: Mapped[Optional[str]] = mapped_column(String(64))
    shipping_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = now_utc()
