"""
ARQ task definitions.

All long-running and recurring commerce automations are defined here.
Each function is an ARQ coroutine - it receives `ctx` as first arg (contains
the Redis pool and any startup context), plus typed keyword arguments.

Tasks are enqueued from API handlers or scheduled via ARQ's cron jobs.
"""
import logging
from datetime import datetime
from typing import Any, Optional

from arq import ArqRedis

from core.utils.log import BackLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider / autobot tasks
# ---------------------------------------------------------------------------

async def run_provider_autobot(
    ctx: dict,
    *,
    user_id: str,
    provider_name: str,
    identifier_name: str,
) -> dict:
    """Poll a provider account for new events and act on them."""
    from db.cruds.users import get_user_data
    from providers.bridge import bridge

    try:
        user_data = get_user_data(user_id)
        provider_data = (
            user_data.get(provider_name, {}).get(identifier_name) if user_data else None
        )
        option = {"namespace": f"{provider_name}_{user_id}_{identifier_name}"}
        await bridge.start_autobot(
            user_id, provider_name, identifier_name, provider_data, option
        )
        return {"status": "ok", "user_id": user_id, "provider": provider_name}
    except Exception as e:
        logger.exception("run_provider_autobot failed: %s", e)
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Shopify tasks
# ---------------------------------------------------------------------------

async def sync_shopify_orders(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    since_hours: int = 24,
) -> dict:
    from optimization.customer_support.handler import CustomerSupportHandler
    from providers.bridge import bridge
    from db.cruds.users import get_user_data

    try:
        user_data = get_user_data(user_id)
        store_data = user_data.get("shopifyprovider", {}).get(store_id, {})
        result = await bridge.get_purchased_products(
            user_id, "shopifyprovider", store_id, store_data, {"since_hours": since_hours}
        )
        return {"status": "ok", "synced": result}
    except Exception as e:
        logger.exception("sync_shopify_orders failed: %s", e)
        return {"status": "error", "error": str(e)}


async def sync_shopify_inventory(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
) -> dict:
    try:
        from providers.bridge import bridge
        from db.cruds.users import get_user_data

        user_data = get_user_data(user_id)
        store_data = user_data.get("shopifyprovider", {}).get(store_id, {})
        result = await bridge.get_all_products(
            user_id, "shopifyprovider", store_id, store_data
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("sync_shopify_inventory failed: %s", e)
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Amazon tasks
# ---------------------------------------------------------------------------

async def sync_amazon_orders(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    since_hours: int = 24,
) -> dict:
    try:
        from providers.bridge import bridge
        from db.cruds.users import get_user_data

        user_data = get_user_data(user_id)
        store_data = user_data.get("amazonprovider", {}).get(store_id, {})
        result = await bridge.get_purchased_products(
            user_id, "amazonprovider", store_id, store_data, {"since_hours": since_hours}
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("sync_amazon_orders failed: %s", e)
        return {"status": "error", "error": str(e)}


async def sync_amazon_inventory(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
) -> dict:
    try:
        from providers.bridge import bridge
        from db.cruds.users import get_user_data

        user_data = get_user_data(user_id)
        store_data = user_data.get("amazonprovider", {}).get(store_id, {})
        result = await bridge.get_all_products(
            user_id, "amazonprovider", store_id, store_data
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("sync_amazon_inventory failed: %s", e)
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Optimization tasks
# ---------------------------------------------------------------------------

async def run_repricing(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    provider: str = "shopifyprovider",
    dry_run: bool = False,
) -> dict:
    from optimization.repricing.engine import RepricingEngine

    try:
        engine = RepricingEngine(store_id=store_id, user_id=user_id, provider=provider)
        result = await engine.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_repricing failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_inventory_restock(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    provider: str = "shopifyprovider",
    dry_run: bool = False,
) -> dict:
    from optimization.inventory.restock import RestockEngine

    try:
        engine = RestockEngine(store_id=store_id, user_id=user_id, provider=provider)
        result = await engine.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_inventory_restock failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_amazon_ads_optimization(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    profile_id: str,
    dry_run: bool = False,
) -> dict:
    from optimization.ads.amazon_ads import AmazonAdsOptimizer

    try:
        optimizer = AmazonAdsOptimizer(
            store_id=store_id, user_id=user_id, profile_id=profile_id
        )
        result = await optimizer.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_amazon_ads_optimization failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_meta_ads_optimization(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    ad_account_id: str,
    dry_run: bool = False,
) -> dict:
    from optimization.ads.meta_ads import MetaAdsOptimizer

    try:
        optimizer = MetaAdsOptimizer(
            store_id=store_id, user_id=user_id, ad_account_id=ad_account_id
        )
        result = await optimizer.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_meta_ads_optimization failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_google_ads_optimization(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    customer_id: str,
    dry_run: bool = False,
) -> dict:
    from optimization.ads.google_ads import GoogleAdsOptimizer

    try:
        optimizer = GoogleAdsOptimizer(
            store_id=store_id, user_id=user_id, customer_id=customer_id
        )
        result = await optimizer.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_google_ads_optimization failed: %s", e)
        return {"status": "error", "error": str(e)}


async def handle_customer_support(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    provider: str,
    order_id: Optional[str] = None,
    message_id: Optional[str] = None,
    message_content: str = "",
    channel: str = "email",
) -> dict:
    from optimization.customer_support.handler import CustomerSupportHandler

    try:
        handler = CustomerSupportHandler(store_id=store_id, user_id=user_id, provider=provider)
        result = await handler.handle(
            order_id=order_id,
            message_id=message_id,
            message_content=message_content,
            channel=channel,
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("handle_customer_support failed: %s", e)
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# ARQ worker settings
# ---------------------------------------------------------------------------

async def startup(ctx: dict) -> None:
    logger.info("ARQ worker starting up")


async def shutdown(ctx: dict) -> None:
    logger.info("ARQ worker shutting down")


async def on_job_abort(ctx: dict) -> None:
    """
    Called by ARQ when a job exhausts all retries.
    Fires an alert so failures are never silent.
    """
    job_id = ctx.get("job_id", "unknown")
    job_name = ctx.get("job_name", "unknown")
    error = ctx.get("result", "unknown error")

    logger.error("Job %s (%s) permanently failed: %s", job_id, job_name, error)

    try:
        from services.notifications.notifier import AlertLevel, notifier
        await notifier.send(
            title=f"Background job failed: {job_name}",
            message=f"Job {job_id} failed after all retries.\n\n{error}",
            level=AlertLevel.ERROR,
            details={
                "job_id": job_id,
                "job_name": job_name,
            },
        )
    except Exception as e:
        logger.warning("Could not send job failure alert: %s", e)


async def run_auto_fulfillment(
    ctx: dict,
    *,
    store_id: str,
    user_id: str,
    provider: str = "shopifyprovider",
    dry_run: bool = False,
) -> dict:
    from automation.fulfillment.engine import FulfillmentEngine

    try:
        engine = FulfillmentEngine(store_id=store_id, user_id=user_id, provider=provider)
        result = await engine.run(dry_run=dry_run)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("run_auto_fulfillment failed: %s", e)
        # Fire error alert so the failure is never silent
        from services.notifications.notifier import notifier, AlertLevel
        await notifier.send(
            title="Fulfillment job failed",
            message=str(e),
            level=AlertLevel.ERROR,
            store_id=store_id,
        )
        return {"status": "error", "error": str(e)}


# All task functions the worker can execute
TASK_FUNCTIONS = [
    run_provider_autobot,
    sync_shopify_orders,
    sync_shopify_inventory,
    sync_amazon_orders,
    sync_amazon_inventory,
    run_repricing,
    run_inventory_restock,
    run_amazon_ads_optimization,
    run_meta_ads_optimization,
    run_google_ads_optimization,
    handle_customer_support,
    run_auto_fulfillment,
]
