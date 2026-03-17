"""
Agent tool definitions.

Each tool is two things:
  1. A JSON schema  → what GPT-4o sees when deciding what to call
  2. A Python callable → what actually executes (direct Python, no HTTP)

user_id is NEVER in the tool parameters — it's always injected from the
authenticated session. The LLM cannot specify a different user.

Tools are grouped into:
  - Read  : safe, no confirmation needed
  - Write : always dry_run=True first, then confirm before dry_run=False
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON schemas (what GPT-4o sees)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    # --- Read tools ---
    {
        "type": "function",
        "function": {
            "name": "list_stores",
            "description": "List all connected e-commerce stores for this user (Shopify + Amazon).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_orders",
            "description": "Fetch recent orders for a store. Returns a summary with the most relevant orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string", "description": "The store identifier (e.g. mystore.myshopify.com or Amazon seller ID)"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "since_hours": {"type": "integer", "default": 24, "description": "How many hours back to look"},
                    "status": {"type": "string", "default": "any", "description": "Filter: any, open, closed, cancelled"},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_products",
            "description": "List products in a store with their prices and stock status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "description": "Get current inventory levels for a store. Highlights low-stock and out-of-stock items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Show recent price changes made by the repricing engine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "days": {"type": "integer", "default": 7},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fulfillment_logs",
            "description": "Show recent fulfillment decisions: what was shipped, with which carrier, at what cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_automation_status",
            "description": "Get a summary of what the automation has been doing: recent optimization runs, job statuses, alerts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_fulfillment_rules",
            "description": "List the fulfillment rules configured for a store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                },
                "required": ["store_id", "provider"],
            },
        },
    },
    # --- Write tools (dry_run first) ---
    {
        "type": "function",
        "function": {
            "name": "run_repricing",
            "description": "Run the repricing engine. ALWAYS use dry_run=true first to preview changes, then ask the user to confirm before dry_run=false.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "dry_run": {"type": "boolean", "description": "true = preview only, false = apply changes. ALWAYS start with true."},
                },
                "required": ["store_id", "provider", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_inventory_restock",
            "description": "Analyze inventory and generate restock recommendations using sales velocity and EOQ. Use dry_run=true first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["store_id", "provider", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_fulfillment",
            "description": "Run the auto-fulfillment engine to process unfulfilled orders per the configured rules. Use dry_run=true first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["store_id", "provider", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_amazon_ads",
            "description": "Optimize Amazon Advertising campaigns: adjust bids, harvest keywords, add negatives. Use dry_run=true first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "profile_id": {"type": "string", "description": "Amazon Advertising profile ID"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["store_id", "profile_id", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_meta_ads",
            "description": "Optimize Meta (Facebook/Instagram) ad campaigns: budgets, creative fatigue, lookalikes. Use dry_run=true first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "ad_account_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["store_id", "ad_account_id", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_google_ads",
            "description": "Optimize Google Ads campaigns: bids, negatives, budgets. Use dry_run=true first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "customer_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["store_id", "customer_id", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_fulfillment_rule",
            "description": "Create a new fulfillment rule for a store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "store_id": {"type": "string"},
                    "provider": {"type": "string", "enum": ["shopifyprovider", "amazonprovider"]},
                    "name": {"type": "string"},
                    "priority": {"type": "integer", "default": 100},
                    "action_type": {
                        "type": "string",
                        "enum": ["fulfill", "skip", "flag", "hold"],
                        "description": "fulfill=auto-ship, skip=do nothing (FBA/digital), flag=alert operator, hold=pause",
                    },
                    "conditions": {
                        "type": "object",
                        "description": "Matching conditions: {payment_statuses, fulfillment_types, min_order_value, max_order_value, country_codes}",
                    },
                    "carrier_strategy": {
                        "type": "string",
                        "enum": ["cheapest", "fastest", "overnight", "balanced"],
                        "default": "cheapest",
                    },
                },
                "required": ["store_id", "provider", "name", "action_type"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool callables
# ---------------------------------------------------------------------------

async def tool_list_stores(user_id: str) -> dict:
    from db.cruds.stores import get_stores_for_user
    stores = await get_stores_for_user(user_id)
    return {
        "stores": [
            {
                "identifier": s.identifier,
                "provider": s.provider,
                "shop_domain": s.shop_domain,
                "marketplace_id": s.marketplace_id,
                "currency": s.currency,
                "is_active": s.is_active,
            }
            for s in stores
        ],
        "count": len(stores),
    }


async def tool_get_orders(user_id: str, store_id: str, provider: str,
                          since_hours: int = 24, status: str = "any") -> dict:
    from db.cruds.users import get_user_data
    from providers.bridge import bridge
    from schemas.commerce import OrderListOptions

    user_data = get_user_data(user_id) or {}
    creds = user_data.get(provider, {}).get(store_id, {})
    p = bridge.shared_provider_list.get(provider.lower())
    if not p:
        return {"error": f"Provider {provider} not available"}

    opts = OrderListOptions(since_hours=since_hours, status=status)
    orders = await p.get_orders(creds, opts)

    # Summarize — don't dump 500 raw orders into the context
    summarized = []
    for o in orders[:15]:
        summarized.append({
            "id": o.order_id,
            "status": o.status,
            "payment": o.payment_status,
            "fulfillment": o.fulfillment_status,
            "total": str(o.total_price),
            "items": len(o.line_items or []),
            "customer": (o.customer.name if o.customer else "—"),
            "created": str(o.created_at)[:16] if o.created_at else "",
        })

    return {
        "total_shown": len(summarized),
        "since_hours": since_hours,
        "orders": summarized,
        "note": f"Showing {len(summarized)} of {len(orders)} orders",
    }


async def tool_get_products(user_id: str, store_id: str, provider: str, limit: int = 20) -> dict:
    from db.cruds.users import get_user_data
    from providers.bridge import bridge
    from schemas.commerce import ProductListOptions

    user_data = get_user_data(user_id) or {}
    creds = user_data.get(provider, {}).get(store_id, {})
    p = bridge.shared_provider_list.get(provider.lower())
    if not p:
        return {"error": f"Provider {provider} not available"}

    products = await p.get_all_products(creds, ProductListOptions(limit=limit))

    return {
        "count": len(products),
        "products": [
            {
                "id": pr.external_id,
                "title": pr.title,
                "sku": (pr.variants[0].sku if pr.variants else ""),
                "price": str(pr.variants[0].price if pr.variants else ""),
                "stock": sum(v.inventory_quantity or 0 for v in (pr.variants or [])),
                "status": pr.status,
            }
            for pr in products[:limit]
        ],
    }


async def tool_get_inventory(user_id: str, store_id: str, provider: str) -> dict:
    from db.cruds.users import get_user_data
    from providers.bridge import bridge

    user_data = get_user_data(user_id) or {}
    creds = user_data.get(provider, {}).get(store_id, {})
    p = bridge.shared_provider_list.get(provider.lower())
    if not p or not hasattr(p, "get_inventory_levels"):
        return {"error": "Inventory not available for this provider"}

    levels = await p.get_inventory_levels(creds)
    low_stock = [l for l in levels if (l.available or 0) < 10]
    out_of_stock = [l for l in levels if (l.available or 0) <= 0]

    return {
        "total_skus": len(levels),
        "low_stock_count": len(low_stock),
        "out_of_stock_count": len(out_of_stock),
        "low_stock": [
            {"sku": l.sku, "available": l.available, "location": l.location_id}
            for l in low_stock[:20]
        ],
        "out_of_stock": [
            {"sku": l.sku, "location": l.location_id}
            for l in out_of_stock[:10]
        ],
    }


async def tool_get_price_history(user_id: str, store_id: str, provider: str, days: int = 7) -> dict:
    from datetime import datetime, timedelta
    from sqlalchemy import select, desc
    from db.postgres import db_session
    from db.models.commerce import PriceHistory
    from db.cruds.stores import get_store

    store = await get_store(user_id, provider, store_id)
    if not store:
        return {"error": "Store not found"}

    since = datetime.utcnow() - timedelta(days=days)
    async with db_session() as session:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.store_id == store.id, PriceHistory.created_at >= since)
            .order_by(desc(PriceHistory.created_at))
            .limit(50)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return {
        "days": days,
        "total_changes": len(rows),
        "changes": [
            {
                "sku": r.variant_external_id,
                "old_price": float(r.old_price),
                "new_price": float(r.new_price),
                "delta": round(float(r.new_price) - float(r.old_price), 2),
                "strategy": r.strategy,
                "reason": r.reason,
                "when": str(r.created_at)[:16] if r.created_at else "",
            }
            for r in rows
        ],
    }


async def tool_get_fulfillment_logs(user_id: str, store_id: str, provider: str, limit: int = 20) -> dict:
    from sqlalchemy import select, desc
    from db.postgres import db_session
    from db.models.commerce import FulfillmentLog
    from db.cruds.stores import get_store

    store = await get_store(user_id, provider, store_id)
    if not store:
        return {"error": "Store not found"}

    async with db_session() as session:
        stmt = (
            select(FulfillmentLog)
            .where(FulfillmentLog.store_id == store.id)
            .order_by(desc(FulfillmentLog.created_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        logs = result.scalars().all()

    fulfilled = [l for l in logs if l.status == "success"]
    failed = [l for l in logs if l.status == "failed"]
    total_cost = sum(float(l.shipping_cost or 0) for l in fulfilled)

    return {
        "total": len(logs),
        "fulfilled": len(fulfilled),
        "failed": len(failed),
        "total_shipping_cost": round(total_cost, 2),
        "recent": [
            {
                "order_id": l.order_id,
                "status": l.status,
                "carrier": l.carrier,
                "tracking": l.tracking_number,
                "cost": float(l.shipping_cost or 0),
                "when": str(l.created_at)[:16] if l.created_at else "",
            }
            for l in logs[:10]
        ],
    }


async def tool_get_automation_status(user_id: str, store_id: str, provider: str) -> dict:
    from sqlalchemy import select, desc
    from db.postgres import db_session
    from db.models.commerce import AutomationRun
    from db.cruds.stores import get_store

    store = await get_store(user_id, provider, store_id)
    if not store:
        return {"error": "Store not found"}

    async with db_session() as session:
        stmt = (
            select(AutomationRun)
            .where(AutomationRun.store_id == store.id)
            .order_by(desc(AutomationRun.created_at))
            .limit(20)
        )
        result = await session.execute(stmt)
        runs = result.scalars().all()

    return {
        "recent_jobs": [
            {
                "job": r.job_name,
                "status": r.status,
                "when": str(r.created_at)[:16] if r.created_at else "",
                "summary": (r.result or {}).get("summary", ""),
            }
            for r in runs
        ]
    }


async def tool_list_fulfillment_rules(user_id: str, store_id: str, provider: str) -> dict:
    from sqlalchemy import select
    from db.postgres import db_session
    from db.models.commerce import FulfillmentRule
    from db.cruds.stores import get_store

    store = await get_store(user_id, provider, store_id)
    if not store:
        return {"error": "Store not found"}

    async with db_session() as session:
        result = await session.execute(
            select(FulfillmentRule)
            .where(FulfillmentRule.store_id == store.id)
            .order_by(FulfillmentRule.priority)
        )
        rules = result.scalars().all()

    return {
        "rules": [
            {
                "id": str(r.id),
                "name": r.name,
                "priority": r.priority,
                "action_type": r.action_type.value,
                "is_active": r.is_active,
                "conditions": r.conditions,
            }
            for r in rules
        ]
    }


async def tool_run_repricing(user_id: str, store_id: str, provider: str, dry_run: bool) -> dict:
    from optimization.repricing.engine import RepricingEngine
    engine = RepricingEngine(store_id=store_id, user_id=user_id, provider=provider)
    return await engine.run(dry_run=dry_run)


async def tool_run_inventory_restock(user_id: str, store_id: str, provider: str, dry_run: bool) -> dict:
    from optimization.inventory.restock import RestockEngine
    engine = RestockEngine(store_id=store_id, user_id=user_id, provider=provider)
    return await engine.run(dry_run=dry_run)


async def tool_run_fulfillment(user_id: str, store_id: str, provider: str, dry_run: bool) -> dict:
    from automation.fulfillment.engine import FulfillmentEngine
    engine = FulfillmentEngine(store_id=store_id, user_id=user_id, provider=provider)
    return await engine.run(dry_run=dry_run)


async def tool_run_amazon_ads(user_id: str, store_id: str, profile_id: str, dry_run: bool) -> dict:
    from optimization.ads.amazon_ads import AmazonAdsOptimizer
    optimizer = AmazonAdsOptimizer(store_id=store_id, user_id=user_id, profile_id=profile_id)
    result = await optimizer.run(dry_run=dry_run)
    return result.model_dump() if hasattr(result, "model_dump") else result


async def tool_run_meta_ads(user_id: str, store_id: str, ad_account_id: str, dry_run: bool) -> dict:
    from optimization.ads.meta_ads import MetaAdsOptimizer
    optimizer = MetaAdsOptimizer(store_id=store_id, user_id=user_id, ad_account_id=ad_account_id)
    result = await optimizer.run(dry_run=dry_run)
    return result.model_dump() if hasattr(result, "model_dump") else result


async def tool_run_google_ads(user_id: str, store_id: str, customer_id: str, dry_run: bool) -> dict:
    from optimization.ads.google_ads import GoogleAdsOptimizer
    optimizer = GoogleAdsOptimizer(store_id=store_id, user_id=user_id, customer_id=customer_id)
    result = await optimizer.run(dry_run=dry_run)
    return result.model_dump() if hasattr(result, "model_dump") else result


async def tool_create_fulfillment_rule(
    user_id: str, store_id: str, provider: str, name: str,
    action_type: str, conditions: dict = None, carrier_strategy: str = "cheapest",
    priority: int = 100,
) -> dict:
    from db.models.commerce import FulfillmentRule, FulfillmentActionType
    from db.postgres import db_session
    from db.cruds.stores import get_store

    store = await get_store(user_id, provider, store_id)
    if not store:
        return {"error": "Store not found"}

    rule = FulfillmentRule(
        store_id=store.id,
        name=name,
        priority=priority,
        action_type=FulfillmentActionType(action_type),
        conditions=conditions or {"payment_statuses": ["paid"]},
        action_config={"carrier_strategy": carrier_strategy, "notify_customer": True},
    )
    async with db_session() as session:
        session.add(rule)

    return {"created": True, "rule_id": str(rule.id), "name": name, "action_type": action_type}


# ---------------------------------------------------------------------------
# Dispatch table — maps tool name → callable
# ---------------------------------------------------------------------------

TOOL_DISPATCH: dict[str, Any] = {
    "list_stores": tool_list_stores,
    "get_orders": tool_get_orders,
    "get_products": tool_get_products,
    "get_inventory": tool_get_inventory,
    "get_price_history": tool_get_price_history,
    "get_fulfillment_logs": tool_get_fulfillment_logs,
    "get_automation_status": tool_get_automation_status,
    "list_fulfillment_rules": tool_list_fulfillment_rules,
    "run_repricing": tool_run_repricing,
    "run_inventory_restock": tool_run_inventory_restock,
    "run_fulfillment": tool_run_fulfillment,
    "run_amazon_ads": tool_run_amazon_ads,
    "run_meta_ads": tool_run_meta_ads,
    "run_google_ads": tool_run_google_ads,
    "create_fulfillment_rule": tool_create_fulfillment_rule,
}


async def execute_tool(name: str, arguments: str, user_id: str) -> str:
    """
    Execute a tool by name. user_id is always injected — never taken from arguments.
    Returns a JSON string suitable for adding to the OpenAI messages list.
    """
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        args["user_id"] = user_id  # always override — never trust LLM-supplied user_id
        result = await fn(**args)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.exception("Tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})
