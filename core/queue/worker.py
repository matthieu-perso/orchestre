"""
ARQ worker settings and cron job schedule.
Import this module in worker.py to run the worker.
"""
from arq import cron
from arq.connections import RedisSettings

from core.config import settings
from core.queue.tasks import (
    TASK_FUNCTIONS,
    on_job_abort,
    shutdown,
    startup,
)


def get_redis_settings() -> RedisSettings:
    url = settings.REDIS_URL
    url = url.replace("redis://", "")
    host = "localhost"
    port = 6379
    password = None

    if "@" in url:
        password, url = url.rsplit("@", 1)
        if ":" in password:
            _, password = password.split(":", 1)

    if "/" in url:
        url, _ = url.rsplit("/", 1)

    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        port = int(port_str)
    else:
        host = url

    return RedisSettings(host=host, port=port, password=password)


# ---------------------------------------------------------------------------
# Cron job functions
# Cron jobs for recurring per-store automations are enqueued dynamically
# from these top-level coroutines, which fan out to all active stores.
# ---------------------------------------------------------------------------

async def run_repricing_cron(ctx: dict) -> dict:
    """Hourly: reprice all active stores."""
    from db.cruds.stores import get_stores_for_user
    from arq import ArqRedis
    from db.cruds.users import get_all_users_data

    pool: ArqRedis = ctx["redis"]
    enqueued = 0
    for user_row in (get_all_users_data() or []):
        for uid, _ in user_row.items():
            try:
                stores = await get_stores_for_user(uid)
                for store in stores:
                    if store.provider in ("shopifyprovider", "amazonprovider"):
                        await pool.enqueue_job(
                            "run_repricing",
                            store_id=store.identifier,
                            user_id=uid,
                            provider=store.provider,
                            dry_run=False,
                        )
                        enqueued += 1
            except Exception:
                pass
    return {"enqueued": enqueued}


async def run_inventory_cron(ctx: dict) -> dict:
    """Every 6h: check restock for all active stores."""
    from db.cruds.stores import get_stores_for_user
    from db.cruds.users import get_all_users_data

    pool = ctx["redis"]
    enqueued = 0
    for user_row in (get_all_users_data() or []):
        for uid, _ in user_row.items():
            try:
                stores = await get_stores_for_user(uid)
                for store in stores:
                    await pool.enqueue_job(
                        "run_inventory_restock",
                        store_id=store.identifier,
                        user_id=uid,
                        provider=store.provider,
                        dry_run=False,
                    )
                    enqueued += 1
            except Exception:
                pass
    return {"enqueued": enqueued}


async def run_ads_cron(ctx: dict) -> dict:
    """Twice daily: optimize ads for all active stores."""
    return {"note": "Configure per-store ad account IDs via /optimize/ads/*/optimize"}


async def run_fulfillment_cron(ctx: dict) -> dict:
    """Every 10 minutes: auto-fulfill eligible orders for all active stores."""
    from db.cruds.stores import get_stores_for_user
    from db.cruds.users import get_all_users_data

    pool = ctx["redis"]
    enqueued = 0
    for user_row in (get_all_users_data() or []):
        for uid, _ in user_row.items():
            try:
                stores = await get_stores_for_user(uid)
                for store in stores:
                    if store.provider in ("shopifyprovider", "amazonprovider"):
                        await pool.enqueue_job(
                            "run_auto_fulfillment",
                            store_id=store.identifier,
                            user_id=uid,
                            provider=store.provider,
                            dry_run=False,
                        )
                        enqueued += 1
            except Exception:
                pass
    return {"enqueued": enqueued}


CRON_FUNCTIONS = [run_repricing_cron, run_inventory_cron, run_ads_cron, run_fulfillment_cron]


class WorkerSettings:
    """ARQ worker configuration."""

    functions = TASK_FUNCTIONS + CRON_FUNCTIONS
    on_startup = startup
    on_shutdown = shutdown
    on_job_abort = on_job_abort
    redis_settings = get_redis_settings()

    max_tries = 3
    job_timeout = 300  # 5 minutes max per job

    cron_jobs = [
        # Repricing: every hour at :05
        cron(run_repricing_cron, hour=set(range(24)), minute={5}),
        # Inventory: every 6 hours at :15
        cron(run_inventory_cron, hour={0, 6, 12, 18}, minute={15}),
        # Ads: twice daily at :30
        cron(run_ads_cron, hour={6, 18}, minute={30}),
        # Fulfillment: every 10 minutes
        cron(run_fulfillment_cron, minute={0, 10, 20, 30, 40, 50}),
    ]
