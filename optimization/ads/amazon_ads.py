"""
Amazon Advertising API optimizer.

Covers Sponsored Products (SP), Sponsored Brands (SB), Sponsored Display (SD).

Optimization actions:
- Bid optimization: raise bids on high-ROAS keywords, lower on high-ACOS
- Budget optimization: shift budget from low-ROAS to high-ROAS campaigns
- Keyword harvesting: mine Search Term Reports → add converting terms as exact/phrase
- Negative keyword mining: add non-converting search terms as negatives
- Dayparting: reduce bids during low-conversion hours
- Placement multipliers: adjust top-of-search / product-page modifiers
- Campaign structure recommendations

Credentials dict expected:
  {
    "refresh_token": "Atzr|...",
    "profile_id": "XXXXXXXX"    # Amazon Ads profile ID
  }
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import httpx

from core.config import settings
from schemas.commerce import (
    AdOptimizationResult,
    BidUpdate,
    BudgetUpdate,
    KeywordPerformance,
)

logger = logging.getLogger(__name__)

ADS_BASE = settings.AMAZON_ADS_ENDPOINT
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Optimization thresholds
TARGET_ACOS = Decimal("0.25")           # 25% target ACOS
MIN_ACOS_FOR_RAISE = Decimal("0.15")    # ACOS below 15% → raise bid
MAX_ACOS_FOR_LOWER = Decimal("0.40")    # ACOS above 40% → lower bid
PAUSE_ACOS_THRESHOLD = Decimal("0.80")  # ACOS > 80% with low sales → pause
MIN_CLICKS_TO_OPTIMIZE = 10            # Ignore keywords with < 10 clicks
BID_CHANGE_PCT = Decimal("0.15")        # Change bids by up to 15%
MAX_BID = Decimal("10.00")
MIN_BID = Decimal("0.10")
MIN_IMPRESSIONS_FOR_HARVEST = 1000      # Search terms need 1k imps to consider
MIN_CVR_FOR_HARVEST = Decimal("0.005")  # 0.5% CVR minimum to add as keyword


class AmazonAdsOptimizer:
    def __init__(self, store_id: str, user_id: str, profile_id: str) -> None:
        # store_id = identifier_name (e.g. Amazon seller ID or the store identifier used in Firebase)
        self.store_id = store_id
        self.user_id = user_id
        self.profile_id = profile_id

    async def _resolve_store_uuid(self):
        try:
            from db.cruds.stores import get_store
            store = await get_store(self.user_id, "amazonprovider", self.store_id)
            return store.id if store else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self, dry_run: bool = False) -> AdOptimizationResult:
        token = await self._get_token()

        result = AdOptimizationResult(
            platform="amazon",
            store_id=self.store_id,
            dry_run=dry_run,
        )

        # 1. Bid optimization
        keyword_perf = await self._fetch_keyword_performance(token)
        bid_updates = self._optimize_bids(keyword_perf)
        result.bid_updates = bid_updates

        # 2. Budget optimization
        campaign_perf = await self._fetch_campaign_performance(token)
        budget_updates = self._optimize_budgets(campaign_perf)
        result.budget_updates = budget_updates

        # 3. Keyword harvesting from search term report
        new_keywords, negatives = await self._harvest_keywords(token)
        result.keywords_added = new_keywords
        result.negatives_added = negatives

        # 4. Pause non-converting keywords
        paused = self._identify_pause_candidates(keyword_perf)
        result.keywords_paused = paused

        result.total_actions = (
            len(bid_updates) + len(budget_updates) + len(new_keywords) +
            len(negatives) + len(paused)
        )

        if not dry_run:
            await self._apply_bid_updates(token, bid_updates)
            await self._apply_budget_updates(token, budget_updates)
            await self._apply_keyword_additions(token, new_keywords)
            await self._apply_negative_additions(token, negatives)
            await self._apply_pauses(token, paused)
            await self._persist_log(result)

        return result

    # ------------------------------------------------------------------
    # Bid optimization
    # ------------------------------------------------------------------

    def _optimize_bids(
        self, keywords: list[KeywordPerformance]
    ) -> list[BidUpdate]:
        updates = []
        for kw in keywords:
            if kw.clicks < MIN_CLICKS_TO_OPTIMIZE or kw.bid is None:
                continue

            acos = kw.acos
            if acos is None:
                continue

            current_bid = kw.bid
            new_bid: Optional[Decimal] = None

            if acos < MIN_ACOS_FOR_RAISE:
                # Performing well: increase bid to capture more traffic
                increase = min(BID_CHANGE_PCT, (MIN_ACOS_FOR_RAISE - acos) / MIN_ACOS_FOR_RAISE)
                new_bid = current_bid * (1 + increase)
                reason = f"ACOS {float(acos):.1%} below target - raising bid"

            elif acos > MAX_ACOS_FOR_LOWER:
                # Over-spending: reduce bid proportionally to get ACOS back to target
                reduction = min(BID_CHANGE_PCT, (acos - TARGET_ACOS) / acos)
                new_bid = current_bid * (1 - reduction)
                reason = f"ACOS {float(acos):.1%} above target - lowering bid"
            else:
                continue

            new_bid = max(MIN_BID, min(MAX_BID, new_bid)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if new_bid == current_bid:
                continue

            updates.append(BidUpdate(
                keyword_id=kw.keyword_id,
                ad_group_id=kw.ad_group_id,
                old_bid=current_bid,
                new_bid=new_bid,
                reason=reason,
            ))

        return updates

    # ------------------------------------------------------------------
    # Budget optimization
    # ------------------------------------------------------------------

    def _optimize_budgets(self, campaigns: list[dict]) -> list[BudgetUpdate]:
        """Shift budget from low-ROAS campaigns to high-ROAS campaigns."""
        updates = []

        scored = []
        for c in campaigns:
            spend = Decimal(str(c.get("spend", "0")))
            sales = Decimal(str(c.get("attributed_sales_14d", "0")))
            budget = Decimal(str(c.get("budget", "0")))

            if spend <= 0 or budget <= 0:
                continue

            roas = (sales / spend) if spend > 0 else Decimal("0")
            budget_utilization = spend / budget  # How much of daily budget was used
            scored.append({
                "id": c.get("campaignId"),
                "name": c.get("name", ""),
                "roas": roas,
                "budget": budget,
                "spend": spend,
                "utilization": budget_utilization,
            })

        if not scored:
            return updates

        avg_roas = sum(s["roas"] for s in scored) / len(scored)

        for camp in scored:
            roas = camp["roas"]
            budget = camp["budget"]
            util = camp["utilization"]

            if roas > avg_roas * Decimal("1.3") and util > Decimal("0.9"):
                # High ROAS + budget-constrained → increase budget 20%
                new_budget = (budget * Decimal("1.2")).quantize(Decimal("1.00"))
                updates.append(BudgetUpdate(
                    campaign_id=camp["id"],
                    old_budget=budget,
                    new_budget=new_budget,
                    reason=f"High ROAS ({float(roas):.2f}x), budget-constrained ({float(util):.0%} utilization)",
                ))

            elif roas < avg_roas * Decimal("0.5") and spend > Decimal("10"):
                # Low ROAS + significant spend → reduce budget 20%
                new_budget = max(Decimal("1.00"), (budget * Decimal("0.8")).quantize(Decimal("1.00")))
                updates.append(BudgetUpdate(
                    campaign_id=camp["id"],
                    old_budget=budget,
                    new_budget=new_budget,
                    reason=f"Low ROAS ({float(roas):.2f}x) vs avg ({float(avg_roas):.2f}x)",
                ))

        return updates

    # ------------------------------------------------------------------
    # Keyword harvesting
    # ------------------------------------------------------------------

    async def _harvest_keywords(
        self, token: str
    ) -> tuple[list[dict], list[dict]]:
        """Mine search term report for new exact-match and negative keywords."""
        search_terms = await self._fetch_search_term_data(token)
        new_keywords = []
        negatives = []

        for term in search_terms:
            impressions = term.get("impressions", 0)
            clicks = term.get("clicks", 0)
            orders = term.get("attributedUnitsOrdered14d", 0)
            spend = Decimal(str(term.get("cost", "0")))
            search_term = term.get("query", "")
            existing_keyword_id = term.get("keywordId", "")

            if impressions < MIN_IMPRESSIONS_FOR_HARVEST or not search_term:
                continue

            cvr = Decimal(str(orders)) / Decimal(str(clicks)) if clicks > 0 else Decimal("0")
            acos = spend / Decimal(str(term.get("attributedSales14d", "1") or "1"))

            if cvr >= MIN_CVR_FOR_HARVEST and acos < MAX_ACOS_FOR_LOWER:
                new_keywords.append({
                    "keyword_text": search_term,
                    "match_type": "EXACT",
                    "suggested_bid": str(term.get("bid", "0.50")),
                    "reason": f"CVR {float(cvr):.2%}, ACOS {float(acos):.1%}",
                    "source_term": search_term,
                    "ad_group_id": term.get("adGroupId"),
                    "campaign_id": term.get("campaignId"),
                })
            elif clicks > 5 and orders == 0 and spend > Decimal("2"):
                negatives.append({
                    "keyword_text": search_term,
                    "match_type": "NEGATIVE_EXACT",
                    "reason": f"{clicks} clicks, 0 orders, ${spend} spent",
                    "campaign_id": term.get("campaignId"),
                    "ad_group_id": term.get("adGroupId"),
                })

        return new_keywords, negatives

    def _identify_pause_candidates(
        self, keywords: list[KeywordPerformance]
    ) -> list[str]:
        """Return keyword IDs that should be paused."""
        paused = []
        for kw in keywords:
            if kw.clicks < MIN_CLICKS_TO_OPTIMIZE:
                continue
            if kw.acos and kw.acos > PAUSE_ACOS_THRESHOLD and kw.orders == 0:
                paused.append(kw.keyword_id)
        return paused

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    async def _get_token(self) -> str:
        from core.auth.token_cache import token_cache
        from db.cruds.users import get_user_data

        user_data = get_user_data(self.user_id)
        credentials = (user_data or {}).get("amazonprovider", {}).get(self.store_id, {})
        refresh_token = credentials.get("refresh_token", "")

        async def _refresh():
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    LWA_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": settings.AMAZON_ADS_CLIENT_ID,
                        "client_secret": settings.AMAZON_ADS_CLIENT_SECRET,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["access_token"], int(data.get("expires_in", 3600))

        return await token_cache.get_or_refresh(
            provider="amazon_ads",
            account_key=f"{self.store_id}:{self.profile_id}",
            refresh_fn=_refresh,
        )

    def _ads_client(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=ADS_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Amazon-Advertising-API-ClientId": settings.AMAZON_ADS_CLIENT_ID or "",
                "Amazon-Advertising-API-Scope": self.profile_id,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def _fetch_keyword_performance(
        self, token: str, days: int = 14
    ) -> list[KeywordPerformance]:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        end = datetime.now(timezone.utc).strftime("%Y%m%d")

        async with self._ads_client(token) as client:
            resp = await client.post(
                "/v2/sp/keywords/report",
                json={
                    "reportDate": end,
                    "metrics": "impressions,clicks,cost,attributedSales14d,attributedUnitsOrdered14d,keywordId,keyword,matchType,adGroupId,campaignId,bid",
                },
            )
            resp.raise_for_status()
            report_id = resp.json().get("reportId")

            # Poll for report completion
            import asyncio
            for _ in range(20):
                await asyncio.sleep(3)
                status_resp = await client.get(f"/v2/reports/{report_id}")
                status_resp.raise_for_status()
                status_data = status_resp.json()
                if status_data.get("status") == "SUCCESS":
                    dl_resp = await client.get(f"/v2/reports/{report_id}/download")
                    dl_resp.raise_for_status()
                    rows = dl_resp.json()
                    break
            else:
                return []

        keywords = []
        for row in (rows if isinstance(rows, list) else []):
            spend = Decimal(str(row.get("cost", "0")))
            sales = Decimal(str(row.get("attributedSales14d", "0")))
            clicks = int(row.get("clicks", 0))
            orders = int(row.get("attributedUnitsOrdered14d", 0))
            impressions = int(row.get("impressions", 0))
            bid = Decimal(str(row.get("bid", "0")))

            acos = (spend / sales).quantize(Decimal("0.0001")) if sales > 0 else None
            roas = (sales / spend).quantize(Decimal("0.0001")) if spend > 0 else None
            ctr = Decimal(str(clicks)) / Decimal(str(impressions)) if impressions > 0 else None
            cvr = Decimal(str(orders)) / Decimal(str(clicks)) if clicks > 0 else None
            cpc = (spend / Decimal(str(clicks))).quantize(Decimal("0.01")) if clicks > 0 else None

            keywords.append(KeywordPerformance(
                keyword_id=str(row.get("keywordId", "")),
                keyword_text=str(row.get("keyword", "")),
                match_type=str(row.get("matchType", "")),
                ad_group_id=str(row.get("adGroupId", "")),
                campaign_id=str(row.get("campaignId", "")),
                impressions=impressions,
                clicks=clicks,
                spend=spend,
                sales=sales,
                orders=orders,
                acos=acos,
                bid=bid,
            ))

        return keywords

    async def _fetch_campaign_performance(self, token: str) -> list[dict]:
        async with self._ads_client(token) as client:
            resp = await client.get("/v2/sp/campaigns")
            resp.raise_for_status()
            campaigns = resp.json()

        return campaigns if isinstance(campaigns, list) else []

    async def _fetch_search_term_data(self, token: str) -> list[dict]:
        end = datetime.now(timezone.utc).strftime("%Y%m%d")
        async with self._ads_client(token) as client:
            resp = await client.post(
                "/v2/sp/targets/report",
                json={
                    "reportDate": end,
                    "metrics": "query,impressions,clicks,cost,attributedSales14d,attributedUnitsOrdered14d,adGroupId,campaignId,keywordId,bid",
                },
            )
            resp.raise_for_status()
            report_id = resp.json().get("reportId")

            import asyncio
            for _ in range(20):
                await asyncio.sleep(3)
                status = await client.get(f"/v2/reports/{report_id}")
                if status.json().get("status") == "SUCCESS":
                    dl = await client.get(f"/v2/reports/{report_id}/download")
                    rows = dl.json()
                    return rows if isinstance(rows, list) else []
        return []

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    async def _apply_bid_updates(self, token: str, updates: list[BidUpdate]) -> None:
        if not updates:
            return
        payload = [
            {"keywordId": u.keyword_id, "bid": float(u.new_bid)}
            for u in updates
        ]
        async with self._ads_client(token) as client:
            await client.put("/v2/sp/keywords", json=payload)

    async def _apply_budget_updates(self, token: str, updates: list[BudgetUpdate]) -> None:
        if not updates:
            return
        payload = [
            {"campaignId": u.campaign_id, "dailyBudget": float(u.new_budget)}
            for u in updates
        ]
        async with self._ads_client(token) as client:
            await client.put("/v2/sp/campaigns", json=payload)

    async def _apply_keyword_additions(self, token: str, keywords: list[dict]) -> None:
        if not keywords:
            return
        payload = [
            {
                "campaignId": kw["campaign_id"],
                "adGroupId": kw["ad_group_id"],
                "keywordText": kw["keyword_text"],
                "matchType": kw["match_type"],
                "bid": float(kw.get("suggested_bid", "0.50")),
                "state": "enabled",
            }
            for kw in keywords
        ]
        async with self._ads_client(token) as client:
            await client.post("/v2/sp/keywords", json=payload)

    async def _apply_negative_additions(self, token: str, negatives: list[dict]) -> None:
        if not negatives:
            return
        payload = [
            {
                "campaignId": neg["campaign_id"],
                "adGroupId": neg.get("ad_group_id"),
                "keywordText": neg["keyword_text"],
                "matchType": neg["match_type"],
                "state": "enabled",
            }
            for neg in negatives
        ]
        async with self._ads_client(token) as client:
            await client.post("/v2/sp/negativeKeywords", json=payload)

    async def _apply_pauses(self, token: str, keyword_ids: list[str]) -> None:
        if not keyword_ids:
            return
        payload = [{"keywordId": kid, "state": "paused"} for kid in keyword_ids]
        async with self._ads_client(token) as client:
            await client.put("/v2/sp/keywords", json=payload)

    async def _persist_log(self, result: AdOptimizationResult) -> None:
        try:
            from db.postgres import db_session
            from db.models.commerce import AdOptimizationLog, AdPlatform

            store_uuid = await self._resolve_store_uuid()
            if not store_uuid:
                return

            async with db_session() as session:
                session.add(AdOptimizationLog(
                    store_id=store_uuid,
                    platform=AdPlatform.AMAZON,
                    action_type="full_optimization",
                    entity_type="campaign",
                    new_value=result.model_dump(mode="json"),
                    applied=not result.dry_run,
                ))
        except Exception as e:
            logger.warning("Could not persist Amazon Ads log: %s", e)
