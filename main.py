import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from apis.route import api_router
from core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

description = f"""
<i>{settings.PROJECT_NAME}</i> — AI-powered e-commerce automation platform.<br><br>

Automate every aspect of your Shopify or Amazon store:<br>
- <b>Commerce</b>: orders, products, inventory, customers, fulfillment<br>
- <b>Repricing</b>: competitive + AI-driven price optimization<br>
- <b>Inventory</b>: restock forecasting with EOQ<br>
- <b>Ads</b>: Amazon Ads, Meta Ads, Google Ads optimization<br>
- <b>Customer Support</b>: AI response generation + review management<br>
- <b>Webhooks</b>: real-time event processing from Shopify & Amazon<br>
"""


def include_router(app: FastAPI) -> None:
    app.include_router(api_router)


def add_middleware(app: FastAPI) -> None:
    app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_KEY)
    # CORS: in production set CORS_ORIGINS (comma-separated). Empty = allow all (dev).
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] if settings.CORS_ORIGINS else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def application_start() -> FastAPI:
    settings.validate_critical()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=description,
        version=settings.PROJECT_VERSION,
    )
    include_router(app)
    add_middleware(app)

    @app.on_event("startup")
    async def startup() -> None:
        logger.info("Starting up %s v%s", settings.PROJECT_NAME, settings.PROJECT_VERSION)
        if settings.PRODUCTION:
            if not settings.CORS_ORIGINS:
                logger.warning(
                    "PRODUCTION=True but CORS_ORIGINS is empty. "
                    "Set CORS_ORIGINS to your frontend URL(s) for security."
                )
            if not settings.STRICT_WEBHOOK_VERIFICATION:
                logger.warning(
                    "PRODUCTION=True but STRICT_WEBHOOK_VERIFICATION=False. "
                    "Enable for production webhook security."
                )
        try:
            from db.postgres import create_tables
            await create_tables()
            logger.info("Database tables created/verified")
        except Exception as e:
            logger.warning("DB startup warning (non-fatal): %s", e)

    return app


app = application_start()


@app.get("/probe", tags=["health"])
async def probe():
    """Liveness: returns 200 if the process is running."""
    return {"status": "ok", "version": settings.PROJECT_VERSION}


@app.get("/health", tags=["health"])
async def health():
    """
    Readiness: checks Postgres and Redis. Returns 503 if any dependency is down.
    Use for load balancer / orchestrator health checks.
    """
    checks = {"postgres": "unknown", "redis": "unknown"}
    status = 200

    # Postgres
    try:
        from sqlalchemy import text
        from db.postgres import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = str(e)
        status = 503

    # Redis
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = str(e)
        status = 503

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status,
        content={"status": "ok" if status == 200 else "degraded", "checks": checks, "version": settings.PROJECT_VERSION},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
