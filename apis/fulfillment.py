"""
Fulfillment API.

Endpoints:
  GET    /fulfillment/rules              → list all rules for current user's stores
  POST   /fulfillment/rules              → create a new rule
  PUT    /fulfillment/rules/{rule_id}    → update a rule
  DELETE /fulfillment/rules/{rule_id}    → delete a rule
  POST   /fulfillment/run                → manually trigger fulfillment for a store
  GET    /fulfillment/logs               → recent fulfillment logs
  GET    /fulfillment/rates              → preview rates for a shipment (no purchase)
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.utils.message import MessageErr, MessageOK
from db.cruds.stores import get_store
from db.models.commerce import FulfillmentActionType, FulfillmentLog, FulfillmentRule
from db.postgres import db_session
from sqlalchemy import select, desc

from .users import User, get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class RuleConditions(BaseModel):
    """All fields are optional — omit to match any value."""
    payment_statuses: list[str] = ["paid"]
    fulfillment_types: list[str] = []       # [], ["fba"], ["manual"], ["digital"]
    min_order_value: Optional[float] = None
    max_order_value: Optional[float] = None
    product_tags: list[str] = []            # order must contain product with ALL these tags
    country_codes: list[str] = []           # empty = any country
    exclude_country_codes: list[str] = []


class RuleActionConfig(BaseModel):
    carrier_strategy: str = "cheapest"      # cheapest | fastest | overnight | balanced
    preferred_carriers: list[str] = []      # e.g. ["USPS", "UPS"]
    notify_customer: bool = True
    package_weight_oz: float = 16.0         # default 1 lb
    package_length: float = 10.0
    package_width: float = 8.0
    package_height: float = 4.0
    ship_from: Optional[dict] = None        # overrides store-level ship_from


class CreateRuleRequest(BaseModel):
    store_identifier: str                   # e.g. "mystore.myshopify.com"
    provider: str = "shopifyprovider"
    name: str
    description: Optional[str] = None
    priority: int = 100
    action_type: FulfillmentActionType = FulfillmentActionType.FULFILL
    conditions: RuleConditions = RuleConditions()
    action_config: RuleActionConfig = RuleActionConfig()


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    action_type: Optional[FulfillmentActionType] = None
    conditions: Optional[RuleConditions] = None
    action_config: Optional[RuleActionConfig] = None


class RatePreviewRequest(BaseModel):
    to_address: dict   # {name, street1, city, state, zip, country}
    from_address: dict
    weight_oz: float = 16.0
    length: float = 10.0
    width: float = 8.0
    height: float = 4.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule_to_dict(rule: FulfillmentRule) -> dict:
    return {
        "rule_id": str(rule.id),
        "store_id": str(rule.store_id),
        "name": rule.name,
        "description": rule.description,
        "priority": rule.priority,
        "is_active": rule.is_active,
        "action_type": rule.action_type.value,
        "conditions": rule.conditions,
        "action_config": rule.action_config,
        "created_at": rule.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# List rules
# ---------------------------------------------------------------------------

@router.get(
    "/rules",
    summary="List fulfillment rules",
    description="Returns all fulfillment rules for the current user's stores, ordered by priority.",
)
async def list_rules(
    provider: str = "shopifyprovider",
    store_identifier: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        store = await get_store(curr_user["uid"], provider, store_identifier)
        if not store:
            return MessageErr(reason="Store not found")

        async with db_session() as session:
            stmt = (
                select(FulfillmentRule)
                .where(FulfillmentRule.store_id == store.id)
                .order_by(FulfillmentRule.priority.asc())
            )
            result = await session.execute(stmt)
            rules = result.scalars().all()

        return MessageOK(data={"rules": [_rule_to_dict(r) for r in rules]})
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Create rule
# ---------------------------------------------------------------------------

@router.post(
    "/rules",
    summary="Create a fulfillment rule",
    description="""
Create a rule that controls how orders are auto-fulfilled.

**action_type values:**
- `fulfill` — buy a shipping label and confirm fulfillment automatically
- `skip` — do nothing (use for FBA orders, digital goods)
- `flag` — send an alert and wait for manual fulfillment
- `hold` — pause auto-fulfillment (logged but no action taken)

**carrier_strategy values:**
- `cheapest` — lowest cost across all carriers
- `fastest` — lowest transit days
- `overnight` — next-day delivery services
- `balanced` — cheapest option with ≤ 3 day transit

**Examples of useful rules:**
1. Priority=10, type=skip, conditions={fulfillment_types:["fba"]} → skip all FBA orders
2. Priority=20, type=fulfill, conditions={country_codes:["US"]}, strategy=cheapest → fulfill US orders cheap
3. Priority=30, type=fulfill, strategy=fastest → fulfill everything else fast
4. Priority=999, type=flag → catch-all: flag any order that didn't match earlier rules
""",
)
async def create_rule(
    body: CreateRuleRequest,
    curr_user: User = Depends(get_current_user),
):
    try:
        store = await get_store(curr_user["uid"], body.provider, body.store_identifier)
        if not store:
            return MessageErr(reason=f"Store '{body.store_identifier}' not found. Register it first via /stores/register")

        rule = FulfillmentRule(
            store_id=store.id,
            name=body.name,
            description=body.description,
            priority=body.priority,
            action_type=body.action_type,
            conditions=body.conditions.model_dump(),
            action_config=body.action_config.model_dump(),
        )

        async with db_session() as session:
            session.add(rule)

        return MessageOK(data={
            "message": "Rule created",
            "rule": _rule_to_dict(rule),
        })
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Update rule
# ---------------------------------------------------------------------------

@router.put(
    "/rules/{rule_id}",
    summary="Update a fulfillment rule",
)
async def update_rule(
    rule_id: str,
    body: UpdateRuleRequest,
    curr_user: User = Depends(get_current_user),
):
    try:
        rid = uuid.UUID(rule_id)
        async with db_session() as session:
            result = await session.execute(
                select(FulfillmentRule).where(FulfillmentRule.id == rid)
            )
            rule = result.scalar_one_or_none()
            if not rule:
                return MessageErr(reason="Rule not found")

            # Verify ownership via store
            store_result = await session.execute(
                select(FulfillmentRule).where(
                    FulfillmentRule.id == rid,
                )
            )
            # Lazy ownership check — verify user owns the store
            from db.cruds.stores import get_store_by_id
            owning_store = await get_store_by_id(rule.store_id)
            if not owning_store or owning_store.user_id != curr_user["uid"]:
                return MessageErr(reason="Rule not found or access denied")

            if body.name is not None:
                rule.name = body.name
            if body.description is not None:
                rule.description = body.description
            if body.priority is not None:
                rule.priority = body.priority
            if body.is_active is not None:
                rule.is_active = body.is_active
            if body.action_type is not None:
                rule.action_type = body.action_type
            if body.conditions is not None:
                rule.conditions = body.conditions.model_dump()
            if body.action_config is not None:
                rule.action_config = body.action_config.model_dump()

            session.add(rule)

        return MessageOK(data={"message": "Rule updated", "rule": _rule_to_dict(rule)})
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Delete rule
# ---------------------------------------------------------------------------

@router.delete(
    "/rules/{rule_id}",
    summary="Delete a fulfillment rule",
)
async def delete_rule(
    rule_id: str,
    curr_user: User = Depends(get_current_user),
):
    try:
        rid = uuid.UUID(rule_id)
        async with db_session() as session:
            result = await session.execute(
                select(FulfillmentRule).where(FulfillmentRule.id == rid)
            )
            rule = result.scalar_one_or_none()
            if not rule:
                return MessageErr(reason="Rule not found")

            from db.cruds.stores import get_store_by_id
            owning_store = await get_store_by_id(rule.store_id)
            if not owning_store or owning_store.user_id != curr_user["uid"]:
                return MessageErr(reason="Rule not found or access denied")

            await session.delete(rule)

        return MessageOK(data={"message": f"Rule {rule_id} deleted"})
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Manually trigger fulfillment
# ---------------------------------------------------------------------------

@router.post(
    "/run",
    summary="Trigger fulfillment engine",
    description="Manually run the auto-fulfillment engine for a store. "
                "Use dry_run=true to preview decisions without creating labels.",
)
async def run_fulfillment(
    store_identifier: str,
    provider: str = "shopifyprovider",
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            from arq import create_pool
            from core.queue.worker import get_redis_settings
            pool = await create_pool(get_redis_settings())
            try:
                job = await pool.enqueue_job(
                    "run_auto_fulfillment",
                    store_id=store_identifier,
                    user_id=curr_user["uid"],
                    provider=provider,
                    dry_run=dry_run,
                )
            finally:
                await pool.aclose()
            return MessageOK(data={"job_id": job.job_id if job else None, "status": "queued"})

        from automation.fulfillment.engine import FulfillmentEngine
        engine = FulfillmentEngine(
            store_id=store_identifier,
            user_id=curr_user["uid"],
            provider=provider,
        )
        result = await engine.run(dry_run=dry_run)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Fulfillment logs
# ---------------------------------------------------------------------------

@router.get(
    "/logs",
    summary="Get fulfillment logs",
    description="Returns the most recent fulfillment decisions made by the engine.",
)
async def get_logs(
    store_identifier: str,
    provider: str = "shopifyprovider",
    limit: int = 100,
    status: Optional[str] = None,
    curr_user: User = Depends(get_current_user),
):
    try:
        store = await get_store(curr_user["uid"], provider, store_identifier)
        if not store:
            return MessageErr(reason="Store not found")

        async with db_session() as session:
            stmt = (
                select(FulfillmentLog)
                .where(FulfillmentLog.store_id == store.id)
            )
            if status:
                stmt = stmt.where(FulfillmentLog.status == status)
            stmt = stmt.order_by(desc(FulfillmentLog.created_at)).limit(limit)
            result = await session.execute(stmt)
            logs = result.scalars().all()

        return MessageOK(data={
            "logs": [
                {
                    "log_id": str(l.id),
                    "order_id": l.order_id,
                    "action_type": l.action_type,
                    "status": l.status,
                    "tracking_number": l.tracking_number,
                    "carrier": l.carrier,
                    "service": l.service,
                    "shipping_cost": float(l.shipping_cost) if l.shipping_cost else None,
                    "error": l.error,
                    "created_at": l.created_at.isoformat(),
                }
                for l in logs
            ]
        })
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Rate preview (no purchase)
# ---------------------------------------------------------------------------

@router.post(
    "/rates/preview",
    summary="Preview shipping rates",
    description="Fetch all available carrier rates for a shipment without purchasing a label. "
                "Useful for estimating shipping costs before configuring rules.",
)
async def preview_rates(
    body: RatePreviewRequest,
    curr_user: User = Depends(get_current_user),
):
    try:
        from services.fulfillment.carriers import easypost_client
        rates = await easypost_client.get_rates(
            to_address=body.to_address,
            from_address=body.from_address,
            parcel={
                "length": body.length,
                "width": body.width,
                "height": body.height,
                "weight": body.weight_oz,
            },
        )
        return MessageOK(data={
            "rates": [
                {
                    "carrier": r.carrier,
                    "service": r.service,
                    "rate": r.rate,
                    "currency": r.currency,
                    "transit_days": r.transit_days,
                    "delivery_date": r.delivery_date,
                }
                for r in sorted(rates, key=lambda r: r.rate)
            ]
        })
    except Exception as e:
        return MessageErr(reason=str(e))
