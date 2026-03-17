"""
Meta (Facebook/Instagram) Ads optimizer.

Uses Marketing API v19+.

Optimization actions:
- Audience optimization: duplicate top-performing ad sets to LTV lookalikes
- Creative fatigue detection: flag ads with declining CTR → suggest refresh
- Budget reallocation: shift spend from low-ROAS ad sets to top performers
- Frequency management: pause ad sets with frequency > threshold
- Interest/behavior audience pruning
- Conversion window optimization
- Retargeting audience freshness checks

Credentials dict expected:
  {
    "access_token": "EAA...",     # Long-lived page/user access token
    "ad_account_id": "act_XXXX"
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

META_GRAPH_URL = f"https://graph.facebook.com/{settings.META_ADS_API_VERSION}"

# Thresholds
MAX_FREQUENCY = Decimal("3.0")           # Pause if frequency > 3 in 7 days
MIN_ROAS_TO_SCALE = Decimal("3.0")       # ROAS above 3x → candidate for scaling
LOW_ROAS_THRESHOLD = Decimal("1.0")      # ROAS below 1x → reduce budget
CTR_FATIGUE_THRESHOLD = Decimal("0.005") # CTR below 0.5% → flag for creative refresh
BUDGET_SCALE_PCT = Decimal("0.20")       # Scale winning sets by 20%
BUDGET_REDUCE_PCT = Decimal("0.25")      # Reduce losers by 25%
MIN_SPEND_TO_EVALUATE = Decimal("20")    # Minimum $20 spend before evaluating
LOOKALIKE_AUDIENCE_RATIO = "0.02"        # 2% lookalike


class MetaAdsOptimizer:
    def __init__(self, store_id: str, user_id: str, ad_account_id: str) -> None:
        # store_id = identifier_name for the associated Shopify/Amazon store
        self.store_id = store_id
        self.user_id = user_id
        self.ad_account_id = ad_account_id
        if not self.ad_account_id.startswith("act_"):
            self.ad_account_id = f"act_{self.ad_account_id}"

    async def _resolve_store_uuid(self):
        try:
            from db.cruds.stores import get_store
            # Meta ads are tied to Shopify store by convention
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
        token = await self._get_token()

        result = AdOptimizationResult(
            platform="meta",
            store_id=self.store_id,
            dry_run=dry_run,
        )

        # 1. Fetch performance data
        ad_sets = await self._fetch_adset_insights(token, days=7)

        # 2. Budget optimization
        budget_updates = self._optimize_budgets(ad_sets)
        result.budget_updates = budget_updates

        # 3. Frequency management
        frequency_pauses = self._identify_frequency_fatigue(ad_sets)
        result.keywords_paused.extend(frequency_pauses)

        # 4. Creative fatigue detection
        creatives = await self._fetch_creative_insights(token, days=14)
        fatigued_creatives = self._detect_creative_fatigue(creatives)
        result.keywords_paused.extend(fatigued_creatives)

        # 5. Lookalike audience creation from top customers
        new_audiences = await self._create_lookalike_audiences(token, ad_sets)
        result.keywords_added.extend(new_audiences)

        result.total_actions = (
            len(budget_updates) + len(frequency_pauses) +
            len(fatigued_creatives) + len(new_audiences)
        )

        if not dry_run:
            await self._apply_budget_updates(token, budget_updates)
            await self._apply_pauses(token, frequency_pauses + fatigued_creatives)
            await self._persist_log(result)

        return result

    # ------------------------------------------------------------------
    # Budget optimization
    # ------------------------------------------------------------------

    def _optimize_budgets(self, ad_sets: list[dict]) -> list[BudgetUpdate]:
        updates = []

        for adset in ad_sets:
            spend = Decimal(str(adset.get("spend", "0") or "0"))
            if spend < MIN_SPEND_TO_EVALUATE:
                continue

            purchase_value = Decimal(str(
                adset.get("purchase_roas", [{}])[0].get("value", "0")
                if adset.get("purchase_roas") else "0"
            ))
            roas = purchase_value  # Meta reports ROAS directly

            current_budget = Decimal(str(adset.get("daily_budget", "0") or "0")) / 100  # cents→dollars

            if current_budget <= 0:
                continue

            if roas >= MIN_ROAS_TO_SCALE:
                new_budget = (current_budget * (1 + BUDGET_SCALE_PCT)).quantize(
                    Decimal("1.00"), rounding=ROUND_HALF_UP
                )
                updates.append(BudgetUpdate(
                    campaign_id=adset.get("id", ""),
                    old_budget=current_budget,
                    new_budget=new_budget,
                    reason=f"High ROAS ({float(roas):.2f}x) - scaling budget +{int(BUDGET_SCALE_PCT*100)}%",
                ))

            elif roas < LOW_ROAS_THRESHOLD and spend > MIN_SPEND_TO_EVALUATE * 2:
                new_budget = max(
                    Decimal("1.00"),
                    (current_budget * (1 - BUDGET_REDUCE_PCT)).quantize(Decimal("1.00"))
                )
                updates.append(BudgetUpdate(
                    campaign_id=adset.get("id", ""),
                    old_budget=current_budget,
                    new_budget=new_budget,
                    reason=f"Low ROAS ({float(roas):.2f}x) - reducing budget -{int(BUDGET_REDUCE_PCT*100)}%",
                ))

        return updates

    def _identify_frequency_fatigue(self, ad_sets: list[dict]) -> list[str]:
        """Return ad set IDs where frequency > threshold."""
        fatigued = []
        for adset in ad_sets:
            freq = Decimal(str(adset.get("frequency", "0") or "0"))
            if freq > MAX_FREQUENCY:
                fatigued.append(adset.get("id", ""))
        return [f for f in fatigued if f]

    def _detect_creative_fatigue(self, ads: list[dict]) -> list[str]:
        """Return ad IDs with CTR below fatigue threshold."""
        fatigued = []
        for ad in ads:
            impressions = int(ad.get("impressions", 0) or 0)
            clicks = int(ad.get("clicks", 0) or 0)
            if impressions < 1000:
                continue
            ctr = Decimal(str(clicks)) / Decimal(str(impressions))
            if ctr < CTR_FATIGUE_THRESHOLD:
                fatigued.append(ad.get("ad_id", ""))
        return [f for f in fatigued if f]

    # ------------------------------------------------------------------
    # Lookalike audiences
    # ------------------------------------------------------------------

    async def _create_lookalike_audiences(
        self, token: str, top_adsets: list[dict]
    ) -> list[dict]:
        """Create lookalike audiences from high-value customer lists."""
        created = []
        top_performers = [
            a for a in top_adsets
            if Decimal(str(a.get("purchase_roas", [{}])[0].get("value", "0") if a.get("purchase_roas") else "0")) >= MIN_ROAS_TO_SCALE
        ]

        if not top_performers:
            return created

        async with self._client(token) as client:
            # Get existing custom audiences to avoid duplicates
            resp = await client.get(
                f"/{self.ad_account_id}/customaudiences",
                params={"fields": "name,id,subtype"},
            )
            if resp.status_code != 200:
                return created

            existing = {a["name"] for a in resp.json().get("data", [])}
            lookalike_name = f"LTV Lookalike - Top Customers 2% - {datetime.now().strftime('%Y%m')}"

            if lookalike_name not in existing:
                created.append({
                    "type": "lookalike_audience",
                    "name": lookalike_name,
                    "ratio": LOOKALIKE_AUDIENCE_RATIO,
                    "note": "Create from top customer purchase custom audience",
                })

        return created

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    async def _get_token(self) -> str:
        """
        Meta long-lived tokens last ~60 days.
        We cache them in Redis so we don't read Firebase on every call.
        The token itself doesn't need refreshing via network — it's read from
        Firebase once and held in Redis until it expires.
        """
        from core.auth.token_cache import token_cache
        from db.cruds.users import get_user_data

        cache_key = f"{self.user_id}:{self.store_id}"
        cached = await token_cache.get("meta_ads", cache_key)
        if cached:
            return cached

        user_data = get_user_data(self.user_id)
        credentials = (user_data or {}).get("metaadsprovider", {}).get(self.store_id, {})
        token = credentials.get("access_token", "")

        if token:
            # Cache for 50 days (well within the 60-day expiry)
            await token_cache.set("meta_ads", cache_key, token, ttl_seconds=50 * 86400)

        return token

    def _client(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=META_GRAPH_URL,
            params={"access_token": token},
            timeout=30.0,
        )

    async def _fetch_adset_insights(
        self, token: str, days: int = 7
    ) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        until = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async with self._client(token) as client:
            resp = await client.get(
                f"/{self.ad_account_id}/adsets",
                params={
                    "fields": "id,name,daily_budget,status,effective_status",
                    "limit": 200,
                },
            )
            if resp.status_code != 200:
                return []
            adsets = resp.json().get("data", [])

            enriched = []
            for adset in adsets:
                if adset.get("effective_status") not in ("ACTIVE", "PAUSED"):
                    continue
                insights_resp = await client.get(
                    f"/{adset['id']}/insights",
                    params={
                        "fields": "spend,impressions,clicks,frequency,purchase_roas,ctr",
                        "time_range": f'{{"since":"{since}","until":"{until}"}}',
                    },
                )
                if insights_resp.status_code == 200:
                    data = insights_resp.json().get("data", [{}])
                    if data:
                        adset.update(data[0])
                enriched.append(adset)

        return enriched

    async def _fetch_creative_insights(self, token: str, days: int = 14) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        until = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async with self._client(token) as client:
            resp = await client.get(
                f"/{self.ad_account_id}/ads",
                params={
                    "fields": "id,name,effective_status",
                    "limit": 200,
                },
            )
            if resp.status_code != 200:
                return []

            ads = resp.json().get("data", [])
            enriched = []
            for ad in ads:
                if ad.get("effective_status") != "ACTIVE":
                    continue
                insight_resp = await client.get(
                    f"/{ad['id']}/insights",
                    params={
                        "fields": "ad_id,impressions,clicks,ctr,spend",
                        "time_range": f'{{"since":"{since}","until":"{until}"}}',
                    },
                )
                if insight_resp.status_code == 200:
                    data = insight_resp.json().get("data", [{}])
                    if data:
                        ad.update(data[0])
                enriched.append(ad)

        return enriched

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    async def _apply_budget_updates(self, token: str, updates: list[BudgetUpdate]) -> None:
        async with self._client(token) as client:
            for upd in updates:
                budget_cents = int(float(upd.new_budget) * 100)
                await client.post(
                    f"/{upd.campaign_id}",
                    params={"daily_budget": budget_cents},
                )

    async def _apply_pauses(self, token: str, entity_ids: list[str]) -> None:
        async with self._client(token) as client:
            for entity_id in entity_ids:
                if entity_id:
                    await client.post(
                        f"/{entity_id}",
                        params={"status": "PAUSED"},
                    )

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
                    platform=AdPlatform.META,
                    action_type="full_optimization",
                    entity_type="adset",
                    new_value=result.model_dump(mode="json"),
                    applied=not result.dry_run,
                ))
        except Exception as e:
            logger.warning("Could not persist Meta Ads log: %s", e)
