"""
Optimization API endpoints.

Triggers optimization jobs (synchronously or via ARQ queue) and returns results.
All jobs support dry_run=true for preview without applying changes.
"""
from typing import Optional

from fastapi import APIRouter, Depends

from core.utils.message import MessageErr, MessageOK

from .users import User, get_current_user

router = APIRouter()


async def _enqueue(job_name: str, **kwargs):
    from arq import create_pool
    from core.queue.worker import get_redis_settings
    pool = await create_pool(get_redis_settings())
    try:
        job = await pool.enqueue_job(job_name, **kwargs)
        return job.job_id if job else None
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# Repricing
# ---------------------------------------------------------------------------

@router.post(
    "/repricing/run",
    summary="Run repricing optimization",
    description="Analyzes competitor prices and recommends/applies price updates. "
                "Set dry_run=true to preview without applying changes.",
)
async def run_repricing(
    store_id: str,
    provider_name: str = "shopifyprovider",
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            job_id = await _enqueue(
                "run_repricing",
                store_id=store_id,
                user_id=curr_user["uid"],
                provider=provider_name,
                dry_run=dry_run,
            )
            return MessageOK(data={"job_id": job_id, "status": "queued"})

        from optimization.repricing.engine import RepricingEngine
        engine = RepricingEngine(
            store_id=store_id,
            user_id=curr_user["uid"],
            provider=provider_name,
        )
        result = await engine.run(dry_run=dry_run)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


@router.get(
    "/repricing/history",
    summary="Get price change history",
)
async def get_repricing_history(
    store_id: str,
    provider_name: str = "shopifyprovider",
    limit: int = 100,
    curr_user: User = Depends(get_current_user),
):
    try:
        from db.postgres import db_session
        from db.models.commerce import PriceHistory
        from db.cruds.stores import get_store
        from sqlalchemy import select, desc

        store = await get_store(curr_user["uid"], provider_name, store_id)
        if not store:
            return MessageErr(reason="Store not found. Register it first via /stores/register")

        async with db_session() as session:
            stmt = (
                select(PriceHistory)
                .where(PriceHistory.store_id == store.id)
                .order_by(desc(PriceHistory.created_at))
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return MessageOK(data={
            "history": [
                {
                    "product_id": str(r.product_id),
                    "variant_id": r.variant_external_id,
                    "old_price": float(r.old_price),
                    "new_price": float(r.new_price),
                    "strategy": r.strategy,
                    "reason": r.reason,
                    "applied_by": r.applied_by,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        })
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Inventory / Restock
# ---------------------------------------------------------------------------

@router.post(
    "/inventory/restock",
    summary="Run restock analysis",
    description="Calculates sales velocity, days of stock remaining, and restock recommendations.",
)
async def run_restock(
    store_id: str,
    provider_name: str = "shopifyprovider",
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            job_id = await _enqueue(
                "run_inventory_restock",
                store_id=store_id,
                user_id=curr_user["uid"],
                provider=provider_name,
                dry_run=dry_run,
            )
            return MessageOK(data={"job_id": job_id, "status": "queued"})

        from optimization.inventory.restock import RestockEngine
        engine = RestockEngine(
            store_id=store_id,
            user_id=curr_user["uid"],
            provider=provider_name,
        )
        result = await engine.run(dry_run=dry_run)
        return MessageOK(data=result)
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Amazon Ads
# ---------------------------------------------------------------------------

@router.post(
    "/ads/amazon/optimize",
    summary="Run Amazon Ads optimization",
    description="Optimizes bids, budgets, harvests keywords, adds negatives. "
                "Covers Sponsored Products, Brands, and Display.",
)
async def optimize_amazon_ads(
    store_id: str,
    profile_id: str,
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            job_id = await _enqueue(
                "run_amazon_ads_optimization",
                store_id=store_id,
                user_id=curr_user["uid"],
                profile_id=profile_id,
                dry_run=dry_run,
            )
            return MessageOK(data={"job_id": job_id, "status": "queued"})

        from optimization.ads.amazon_ads import AmazonAdsOptimizer
        optimizer = AmazonAdsOptimizer(
            store_id=store_id,
            user_id=curr_user["uid"],
            profile_id=profile_id,
        )
        result = await optimizer.run(dry_run=dry_run)
        return MessageOK(data=result.model_dump())
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Meta Ads
# ---------------------------------------------------------------------------

@router.post(
    "/ads/meta/optimize",
    summary="Run Meta (Facebook/Instagram) Ads optimization",
    description="Optimizes budgets, detects creative fatigue, manages frequency, creates lookalikes.",
)
async def optimize_meta_ads(
    store_id: str,
    ad_account_id: str,
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            job_id = await _enqueue(
                "run_meta_ads_optimization",
                store_id=store_id,
                user_id=curr_user["uid"],
                ad_account_id=ad_account_id,
                dry_run=dry_run,
            )
            return MessageOK(data={"job_id": job_id, "status": "queued"})

        from optimization.ads.meta_ads import MetaAdsOptimizer
        optimizer = MetaAdsOptimizer(
            store_id=store_id,
            user_id=curr_user["uid"],
            ad_account_id=ad_account_id,
        )
        result = await optimizer.run(dry_run=dry_run)
        return MessageOK(data=result.model_dump())
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Google Ads
# ---------------------------------------------------------------------------

@router.post(
    "/ads/google/optimize",
    summary="Run Google Ads optimization",
    description="Mines search terms, adjusts bids (target CPA/ROAS), optimizes Shopping campaigns.",
)
async def optimize_google_ads(
    store_id: str,
    customer_id: str,
    dry_run: bool = True,
    async_mode: bool = False,
    curr_user: User = Depends(get_current_user),
):
    try:
        if async_mode:
            job_id = await _enqueue(
                "run_google_ads_optimization",
                store_id=store_id,
                user_id=curr_user["uid"],
                customer_id=customer_id,
                dry_run=dry_run,
            )
            return MessageOK(data={"job_id": job_id, "status": "queued"})

        from optimization.ads.google_ads import GoogleAdsOptimizer
        optimizer = GoogleAdsOptimizer(
            store_id=store_id,
            user_id=curr_user["uid"],
            customer_id=customer_id,
        )
        result = await optimizer.run(dry_run=dry_run)
        return MessageOK(data=result.model_dump())
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Customer support
# ---------------------------------------------------------------------------

@router.post(
    "/support/handle",
    summary="AI customer support handler",
    description="Classifies, enriches with order context, and generates AI response for a customer message.",
)
async def handle_support(
    store_id: str,
    provider_name: str,
    message_content: str,
    order_id: Optional[str] = None,
    message_id: Optional[str] = None,
    channel: str = "email",
    curr_user: User = Depends(get_current_user),
):
    try:
        from optimization.customer_support.handler import CustomerSupportHandler
        handler = CustomerSupportHandler(
            store_id=store_id,
            user_id=curr_user["uid"],
            provider=provider_name,
        )
        result = await handler.handle(
            order_id=order_id,
            message_id=message_id,
            message_content=message_content,
            channel=channel,
        )
        return MessageOK(data=result.model_dump())
    except Exception as e:
        return MessageErr(reason=str(e))


@router.post(
    "/support/review_response",
    summary="Generate AI review response",
    description="Generates a professional response to a customer review.",
)
async def generate_review_response(
    store_id: str,
    provider_name: str,
    review_text: str,
    rating: int,
    product_name: str = "",
    curr_user: User = Depends(get_current_user),
):
    try:
        from optimization.customer_support.handler import CustomerSupportHandler
        handler = CustomerSupportHandler(
            store_id=store_id,
            user_id=curr_user["uid"],
            provider=provider_name,
        )
        response = await handler.generate_review_response(
            review_text=review_text,
            rating=rating,
            product_name=product_name,
        )
        return MessageOK(data={"response": response})
    except Exception as e:
        return MessageErr(reason=str(e))


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

@router.get(
    "/jobs/{job_id}",
    summary="Get optimization job status",
)
async def get_job_status(
    job_id: str,
    curr_user: User = Depends(get_current_user),
):
    try:
        from arq import create_pool
        from core.queue.worker import get_redis_settings
        pool = await create_pool(get_redis_settings())
        try:
            job_result = await pool.job_result(job_id)
            if job_result:
                return MessageOK(data={
                    "job_id": job_id,
                    "status": "complete",
                    "result": job_result.result,
                    "success": job_result.success,
                })
            return MessageOK(data={"job_id": job_id, "status": "pending_or_not_found"})
        finally:
            await pool.aclose()
    except Exception as e:
        return MessageErr(reason=str(e))
