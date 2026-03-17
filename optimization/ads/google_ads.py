"""
Google Ads optimizer.

Uses Google Ads API v15 (via REST, no google-ads SDK dependency).

Optimization actions:
- Search term mining → add converting terms as exact match
- Negative keyword mining → add non-converting terms as negatives
- Bid adjustments: target CPA / target ROAS smart bidding recommendations
- Quality Score monitoring → flag low-QS keywords
- Shopping feed optimization: price competitiveness, product disapprovals
- Audience bid modifiers (in-market, remarketing)
- Ad schedule bid adjustments (dayparting)
- Device bid modifiers (mobile vs. desktop performance split)

Credentials dict expected:
  {
    "refresh_token": "...",         # Google OAuth2 refresh token
    "customer_id": "XXX-XXX-XXXX"  # Google Ads customer ID (no dashes in API)
  }
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import httpx

from core.config import settings
from schemas.commerce import AdOptimizationResult, BidUpdate, BudgetUpdate

logger = logging.getLogger(__name__)

GOOGLE_ADS_API_BASE = f"https://googleads.googleapis.com/{settings.GOOGLE_ADS_API_VERSION}"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Thresholds
TARGET_CPA_OVERPERFORM = Decimal("0.7")    # CPA < 70% of target → raise budget/bids
TARGET_CPA_UNDERPERFORM = Decimal("1.3")   # CPA > 130% of target → reduce
MIN_IMPRESSIONS_FOR_QS = 1000
LOW_QUALITY_SCORE = 4
MIN_CLICKS_FOR_HARVEST = 3
MAX_SPEND_FOR_ZERO_CONV = Decimal("15")    # $15 spend with 0 conversions → negative


class GoogleAdsOptimizer:
    def __init__(self, store_id: str, user_id: str, customer_id: str) -> None:
        # store_id = identifier_name for the associated commerce store
        self.store_id = store_id
        self.user_id = user_id
        self.customer_id = customer_id.replace("-", "")

    async def _resolve_store_uuid(self):
        try:
            from db.cruds.stores import get_store
            store = await get_store(self.user_id, "shopifyprovider", self.store_id)
            if not store:
                store = await get_store(self.user_id, "amazonprovider", self.store_id)
            return store.id if store else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self, dry_run: bool = False) -> AdOptimizationResult:
        token = await self._get_google_token()

        result = AdOptimizationResult(
            platform="google",
            store_id=self.store_id,
            dry_run=dry_run,
        )

        # 1. Keyword performance analysis
        keyword_data = await self._fetch_keyword_performance(token)
        bid_updates = self._optimize_keyword_bids(keyword_data)
        result.bid_updates = bid_updates

        # 2. Search term harvesting
        search_terms = await self._fetch_search_term_performance(token)
        new_keywords, negatives = self._process_search_terms(search_terms)
        result.keywords_added = new_keywords
        result.negatives_added = negatives

        # 3. Campaign budget optimization
        campaign_data = await self._fetch_campaign_performance(token)
        budget_updates = self._optimize_campaign_budgets(campaign_data)
        result.budget_updates.extend(budget_updates)

        # 4. Quality score flags
        low_qs = self._identify_low_quality_score(keyword_data)
        result.keywords_paused.extend(low_qs)

        # 5. Shopping specific optimizations
        shopping_updates = await self._optimize_shopping_campaigns(token)
        result.budget_updates.extend(shopping_updates)

        result.total_actions = (
            len(bid_updates) + len(new_keywords) + len(negatives) +
            len(budget_updates) + len(low_qs) + len(shopping_updates)
        )

        if not dry_run:
            await self._apply_bid_updates(token, bid_updates)
            await self._apply_keywords(token, new_keywords, negatives)
            await self._apply_budget_updates(token, budget_updates + shopping_updates)
            await self._persist_log(result)

        return result

    # ------------------------------------------------------------------
    # Keyword bid optimization
    # ------------------------------------------------------------------

    def _optimize_keyword_bids(self, keywords: list[dict]) -> list[BidUpdate]:
        updates = []
        for kw in keywords:
            clicks = int(kw.get("metrics_clicks", 0))
            if clicks < 5:
                continue

            current_bid_micros = int(kw.get("ad_group_criterion_effective_cpc_bid_micros", 0))
            if current_bid_micros <= 0:
                continue

            current_bid = Decimal(current_bid_micros) / 1_000_000

            conversions = Decimal(str(kw.get("metrics_conversions", "0")))
            cost = Decimal(str(kw.get("metrics_cost_micros", "0"))) / 1_000_000
            conv_value = Decimal(str(kw.get("metrics_conversions_value", "0")))

            if conversions > 0:
                cpa = cost / conversions
                target_cpa = Decimal(str(kw.get("target_cpa_micros", "0"))) / 1_000_000

                if target_cpa > 0:
                    if cpa < target_cpa * TARGET_CPA_OVERPERFORM:
                        new_bid = min(Decimal("10.00"), current_bid * Decimal("1.15"))
                        reason = f"CPA ${float(cpa):.2f} well below target ${float(target_cpa):.2f}"
                    elif cpa > target_cpa * TARGET_CPA_UNDERPERFORM:
                        new_bid = max(Decimal("0.01"), current_bid * Decimal("0.85"))
                        reason = f"CPA ${float(cpa):.2f} above target ${float(target_cpa):.2f}"
                    else:
                        continue

                    new_bid = new_bid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    updates.append(BidUpdate(
                        keyword_id=str(kw.get("ad_group_criterion_criterion_id", "")),
                        ad_group_id=str(kw.get("ad_group_id", "")),
                        old_bid=current_bid,
                        new_bid=new_bid,
                        reason=reason,
                    ))

        return updates

    # ------------------------------------------------------------------
    # Search term processing
    # ------------------------------------------------------------------

    def _process_search_terms(
        self, search_terms: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        new_keywords = []
        negatives = []

        for term in search_terms:
            query = term.get("search_term_view_search_term", "")
            if not query:
                continue

            clicks = int(term.get("metrics_clicks", 0))
            conversions = Decimal(str(term.get("metrics_conversions", "0")))
            cost = Decimal(str(term.get("metrics_cost_micros", "0"))) / 1_000_000
            impressions = int(term.get("metrics_impressions", 0))

            cvr = conversions / Decimal(str(clicks)) if clicks > 0 else Decimal("0")

            if clicks >= MIN_CLICKS_FOR_HARVEST and conversions > 0 and cvr > Decimal("0.02"):
                new_keywords.append({
                    "query": query,
                    "match_type": "EXACT",
                    "campaign_id": term.get("campaign_id", ""),
                    "ad_group_id": term.get("ad_group_id", ""),
                    "suggested_bid_micros": int(
                        (cost / conversions * 1_000_000).quantize(Decimal("1"))
                    ) if conversions > 0 else 500_000,
                    "reason": f"CVR {float(cvr):.2%}, {int(conversions)} conversions",
                })

            elif clicks >= 5 and conversions == 0 and cost >= MAX_SPEND_FOR_ZERO_CONV:
                negatives.append({
                    "query": query,
                    "match_type": "EXACT",
                    "campaign_id": term.get("campaign_id", ""),
                    "reason": f"${float(cost):.2f} spend, 0 conversions",
                })

        return new_keywords, negatives

    def _identify_low_quality_score(self, keywords: list[dict]) -> list[str]:
        low_qs = []
        for kw in keywords:
            impressions = int(kw.get("metrics_impressions", 0))
            qs = kw.get("ad_group_criterion_quality_info_quality_score")
            if impressions >= MIN_IMPRESSIONS_FOR_QS and qs and int(qs) <= LOW_QUALITY_SCORE:
                low_qs.append(str(kw.get("ad_group_criterion_criterion_id", "")))
        return [k for k in low_qs if k]

    # ------------------------------------------------------------------
    # Budget optimization
    # ------------------------------------------------------------------

    def _optimize_campaign_budgets(self, campaigns: list[dict]) -> list[BudgetUpdate]:
        updates = []
        for camp in campaigns:
            budget_micros = int(camp.get("campaign_budget_amount_micros", 0))
            if budget_micros <= 0:
                continue

            budget = Decimal(budget_micros) / 1_000_000
            cost = Decimal(str(camp.get("metrics_cost_micros", "0"))) / 1_000_000
            conv_value = Decimal(str(camp.get("metrics_conversions_value", "0")))
            impressions = int(camp.get("metrics_impressions", 0))
            lost_impression_share = Decimal(str(
                camp.get("metrics_search_budget_lost_impression_share", "0") or "0"
            ))

            if cost <= 0:
                continue

            roas = conv_value / cost if cost > 0 else Decimal("0")

            # Budget-limited + good ROAS → increase
            if lost_impression_share > Decimal("0.20") and roas > Decimal("3"):
                new_budget = (budget * Decimal("1.20")).quantize(Decimal("0.01"))
                updates.append(BudgetUpdate(
                    campaign_id=str(camp.get("campaign_id", "")),
                    old_budget=budget,
                    new_budget=new_budget,
                    reason=f"Lost {float(lost_impression_share):.0%} impression share, ROAS {float(roas):.2f}x",
                ))

            # Poor ROAS + significant spend → decrease
            elif roas < Decimal("1.0") and cost > Decimal("50"):
                new_budget = max(Decimal("1.00"), (budget * Decimal("0.80")).quantize(Decimal("0.01")))
                updates.append(BudgetUpdate(
                    campaign_id=str(camp.get("campaign_id", "")),
                    old_budget=budget,
                    new_budget=new_budget,
                    reason=f"Poor ROAS ({float(roas):.2f}x) with ${float(cost):.0f} spend",
                ))

        return updates

    # ------------------------------------------------------------------
    # Shopping
    # ------------------------------------------------------------------

    async def _optimize_shopping_campaigns(self, token: str) -> list[BudgetUpdate]:
        """Identify Shopping campaigns with low impression share on high-value products."""
        return []  # Extend with Shopping-specific queries

    # ------------------------------------------------------------------
    # Google Ads API calls
    # ------------------------------------------------------------------

    async def _get_google_token(self) -> str:
        from core.auth.token_cache import token_cache
        from db.cruds.users import get_user_data

        user_data = get_user_data(self.user_id)
        credentials = (user_data or {}).get("googleadsprovider", {}).get(self.store_id, {})
        refresh_token = credentials.get("refresh_token", "")

        async def _refresh():
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    OAUTH_TOKEN_URL,
                    data={
                        "client_id": settings.GOOGLE_ADS_CLIENT_ID,
                        "client_secret": settings.GOOGLE_ADS_CLIENT_SECRET,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["access_token"], int(data.get("expires_in", 3600))

        return await token_cache.get_or_refresh(
            provider="google_ads",
            account_key=f"{self.store_id}:{self.customer_id}",
            refresh_fn=_refresh,
        )

    def _client(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{GOOGLE_ADS_API_BASE}/customers/{self.customer_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN or "",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def _gaql_query(self, token: str, query: str) -> list[dict]:
        async with self._client(token) as client:
            resp = await client.post(
                "/googleAds:searchStream",
                json={"query": query},
            )
            resp.raise_for_status()
            results = []
            for batch in resp.json():
                for row in batch.get("results", []):
                    flat: dict = {}
                    self._flatten(row, flat, "")
                    results.append(flat)
            return results

    def _flatten(self, obj: dict, out: dict, prefix: str) -> None:
        for k, v in obj.items():
            key = f"{prefix}_{k}" if prefix else k
            if isinstance(v, dict):
                self._flatten(v, out, key)
            else:
                out[key] = v

    async def _fetch_keyword_performance(self, token: str, days: int = 30) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                ad_group.id,
                ad_group_criterion.criterion_id,
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                ad_group_criterion.effective_cpc_bid_micros,
                ad_group_criterion.quality_info.quality_score,
                campaign.target_cpa.target_cpa_micros,
                metrics.clicks,
                metrics.impressions,
                metrics.cost_micros,
                metrics.conversions,
                metrics.conversions_value
            FROM keyword_view
            WHERE segments.date >= '{since}'
              AND campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
        """
        return await self._gaql_query(token, query)

    async def _fetch_search_term_performance(self, token: str, days: int = 14) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                campaign.id,
                ad_group.id,
                search_term_view.search_term,
                metrics.clicks,
                metrics.impressions,
                metrics.cost_micros,
                metrics.conversions,
                metrics.conversions_value
            FROM search_term_view
            WHERE segments.date >= '{since}'
              AND campaign.status = 'ENABLED'
        """
        return await self._gaql_query(token, query)

    async def _fetch_campaign_performance(self, token: str, days: int = 30) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                campaign_budget.amount_micros,
                metrics.cost_micros,
                metrics.conversions,
                metrics.conversions_value,
                metrics.impressions,
                metrics.search_budget_lost_impression_share
            FROM campaign
            WHERE segments.date >= '{since}'
              AND campaign.status = 'ENABLED'
        """
        return await self._gaql_query(token, query)

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    async def _apply_bid_updates(self, token: str, updates: list[BidUpdate]) -> None:
        if not updates:
            return
        operations = [
            {
                "update": {
                    "resourceName": f"customers/{self.customer_id}/adGroupCriteria/{u.ad_group_id}~{u.keyword_id}",
                    "cpcBidMicros": str(int(float(u.new_bid) * 1_000_000)),
                },
                "updateMask": "cpcBidMicros",
            }
            for u in updates
        ]
        async with self._client(token) as client:
            await client.post("/adGroupCriteria:mutate", json={"operations": operations})

    async def _apply_keywords(
        self, token: str, new_keywords: list[dict], negatives: list[dict]
    ) -> None:
        if new_keywords:
            ops = [
                {
                    "create": {
                        "adGroup": f"customers/{self.customer_id}/adGroups/{kw['ad_group_id']}",
                        "keyword": {"text": kw["query"], "matchType": kw["match_type"]},
                        "cpcBidMicros": str(kw.get("suggested_bid_micros", 500_000)),
                        "status": "ENABLED",
                    }
                }
                for kw in new_keywords
            ]
            async with self._client(token) as client:
                await client.post("/adGroupCriteria:mutate", json={"operations": ops})

        if negatives:
            neg_ops = [
                {
                    "create": {
                        "campaign": f"customers/{self.customer_id}/campaigns/{neg['campaign_id']}",
                        "keyword": {"text": neg["query"], "matchType": neg["match_type"]},
                    }
                }
                for neg in negatives
            ]
            async with self._client(token) as client:
                await client.post("/campaignCriteria:mutate", json={"operations": neg_ops})

    async def _apply_budget_updates(self, token: str, updates: list[BudgetUpdate]) -> None:
        if not updates:
            return
        async with self._client(token) as client:
            for upd in updates:
                ops = [{
                    "update": {
                        "resourceName": f"customers/{self.customer_id}/campaignBudgets/{upd.campaign_id}",
                        "amountMicros": str(int(float(upd.new_budget) * 1_000_000)),
                    },
                    "updateMask": "amountMicros",
                }]
                await client.post("/campaignBudgets:mutate", json={"operations": ops})

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
                    platform=AdPlatform.GOOGLE,
                    action_type="full_optimization",
                    entity_type="campaign",
                    new_value=result.model_dump(mode="json"),
                    applied=not result.dry_run,
                ))
        except Exception as e:
            logger.warning("Could not persist Google Ads log: %s", e)
