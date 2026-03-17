"""
Repricing Engine.

Workflow per run:
1. Load all active products + their repricing rules
2. For each product/SKU: fetch competitor prices
3. Apply strategy → generate PriceRecommendation
4. Enforce min/max bounds (never below cost, never above ceiling)
5. Persist recommendations + optionally execute price updates
6. Log everything to price_history

Supported strategies:
- COMPETITIVE_LOWEST     : match or beat the lowest competitor price
- COMPETITIVE_BUYBOX     : target the Buy Box price (Amazon) or competitive middle
- RULE_BASED             : fixed markup/markdown rules
- AI_OPTIMIZED           : LLM-driven reasoning over margin, velocity, competition
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from schemas.commerce import CompetitorPrice, PriceRecommendation, PriceUpdate
from core.utils.log import BackLog

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class RepricingEngine:
    def __init__(self, store_id: str, user_id: str, provider: str) -> None:
        # store_id = identifier_name (e.g. "mystore.myshopify.com" or "SELLER_ID")
        # This is the key used in Firebase credentials AND the stores table identifier column.
        self.store_id = store_id
        self.user_id = user_id
        self.provider = provider

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self, dry_run: bool = False) -> dict:
        from db.cruds.users import get_user_data
        from providers.bridge import bridge

        user_data = get_user_data(self.user_id)
        credentials = (user_data or {}).get(self.provider, {}).get(self.store_id, {})

        products = await bridge.get_all_products(
            self.user_id, self.provider, self.store_id, credentials
        )

        rules = await self._load_rules()
        recommendations: list[PriceRecommendation] = []
        updates: list[PriceUpdate] = []

        for product in products:
            for variant in (product.variants or []):
                rule = self._match_rule(rules, variant.sku)
                if rule is None:
                    continue

                competitor_prices = await self._fetch_competitor_prices(
                    bridge, credentials, product, variant
                )

                rec = self._apply_strategy(
                    product_id=product.external_id,
                    variant_id=variant.external_id,
                    sku=variant.sku,
                    current_price=variant.price,
                    cost=variant.cost_per_item,
                    rule=rule,
                    competitor_prices=competitor_prices,
                )
                recommendations.append(rec)

                if not dry_run and rec.recommended_price != rec.current_price:
                    updates.append(PriceUpdate(
                        variant_id=variant.external_id,
                        price=rec.recommended_price,
                    ))

        executed = []
        if updates and not dry_run:
            try:
                executed = await bridge.get_all_products(  # replaced by update call below
                    self.user_id, self.provider, self.store_id, credentials
                )
                # Actually execute price updates via provider
                from providers.bridge import bridge as b
                provider_obj = b.shared_provider_list.get(self.provider.lower())
                if provider_obj and hasattr(provider_obj, "update_product_price"):
                    executed = await provider_obj.update_product_price(credentials, updates)
                    await self._persist_history(recommendations, applied=True)
            except Exception as e:
                logger.exception("Repricing execution failed: %s", e)

        return {
            "recommendations": [r.model_dump() for r in recommendations],
            "executed_updates": len(executed),
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _apply_strategy(
        self,
        product_id: str,
        variant_id: str,
        sku: Optional[str],
        current_price: Decimal,
        cost: Optional[Decimal],
        rule: dict,
        competitor_prices: list[CompetitorPrice],
    ) -> PriceRecommendation:
        strategy = rule.get("strategy", "competitive_buybox")
        min_mult = Decimal(str(rule.get("min_price_multiplier", "0.9")))
        max_mult = Decimal(str(rule.get("max_price_multiplier", "1.2")))
        undercut_pct = Decimal(str(rule.get("undercut_by_pct", "0.5"))) / 100
        target_margin_pct = rule.get("target_margin_pct")

        # Compute bounds
        floor_price = (cost * Decimal("1.05")).quantize(TWO_PLACES, ROUND_HALF_UP) if cost else current_price * min_mult
        min_price = max(floor_price, (current_price * min_mult).quantize(TWO_PLACES, ROUND_HALF_UP))
        max_price = (current_price * max_mult).quantize(TWO_PLACES, ROUND_HALF_UP)

        recommended = current_price
        reason = "No change - no competitor data"

        if strategy == "competitive_lowest" and competitor_prices:
            lowest = min(p.price for p in competitor_prices)
            target = lowest * (1 - undercut_pct)
            recommended = self._clamp(target, min_price, max_price)
            reason = f"Undercut lowest competitor (${lowest}) by {undercut_pct*100:.1f}%"

        elif strategy == "competitive_buybox" and competitor_prices:
            buybox = next((p for p in competitor_prices if p.is_buybox_winner), None)
            if buybox:
                target = buybox.price * (1 - undercut_pct)
                recommended = self._clamp(target, min_price, max_price)
                reason = f"Target Buy Box price (${buybox.price}) -${target_margin_pct or undercut_pct*100:.1f}%"
            elif competitor_prices:
                median = sorted(p.price for p in competitor_prices)[len(competitor_prices) // 2]
                recommended = self._clamp(median, min_price, max_price)
                reason = f"Matched median competitor price (${median})"

        elif strategy == "rule_based":
            if target_margin_pct and cost:
                margin = Decimal(str(target_margin_pct)) / 100
                recommended = (cost / (1 - margin)).quantize(TWO_PLACES, ROUND_HALF_UP)
                recommended = self._clamp(recommended, min_price, max_price)
                reason = f"Rule-based: {target_margin_pct}% target margin"
            else:
                reason = "Rule-based: no cost data"

        elif strategy == "ai_optimized" and competitor_prices:
            recommended = self._ai_price(
                current_price, cost, competitor_prices, min_price, max_price
            )
            reason = "AI-optimized pricing"

        # Round to nearest cent
        recommended = recommended.quantize(TWO_PLACES, ROUND_HALF_UP)

        # Calculate expected margin
        expected_margin = None
        if cost and recommended > 0:
            expected_margin = ((recommended - cost) / recommended * 100).quantize(
                Decimal("0.1"), ROUND_HALF_UP
            )

        return PriceRecommendation(
            product_id=product_id,
            variant_id=variant_id,
            sku=sku,
            current_price=current_price,
            recommended_price=recommended,
            min_price=min_price,
            max_price=max_price,
            strategy=strategy,
            reason=reason,
            competitor_prices=competitor_prices,
            expected_margin_pct=expected_margin,
        )

    def _ai_price(
        self,
        current_price: Decimal,
        cost: Optional[Decimal],
        competitors: list[CompetitorPrice],
        min_price: Decimal,
        max_price: Decimal,
    ) -> Decimal:
        """
        Heuristic AI pricing: blend margin target + competitive position.
        For production: replace with LLM call or ML model.
        """
        prices = sorted(p.price for p in competitors)
        n = len(prices)
        if n == 0:
            return current_price

        # Target 25th-percentile of competition (aggressive)
        idx = max(0, int(n * 0.25) - 1)
        competitive_target = prices[idx]

        if cost:
            # Blend: 60% competition, 40% margin-based (20% margin floor)
            margin_target = (cost * Decimal("1.20"))
            blended = competitive_target * Decimal("0.6") + margin_target * Decimal("0.4")
        else:
            blended = competitive_target

        return self._clamp(blended, min_price, max_price)

    def _clamp(self, value: Decimal, low: Decimal, high: Decimal) -> Decimal:
        return max(low, min(high, value))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_store_uuid(self) -> "Optional[uuid.UUID]":
        """Resolve the DB store UUID from (user_id, provider, identifier_name)."""
        try:
            from db.cruds.stores import get_store
            import uuid
            store = await get_store(self.user_id, self.provider, self.store_id)
            return store.id if store else None
        except Exception:
            return None

    async def _load_rules(self) -> list[dict]:
        """Load repricing rules from DB for this store."""
        try:
            from db.postgres import db_session
            from db.models.commerce import RepricingRule
            from sqlalchemy import select

            store_uuid = await self._resolve_store_uuid()
            if not store_uuid:
                return [self._default_rule()]

            async with db_session() as session:
                stmt = select(RepricingRule).where(
                    RepricingRule.store_id == store_uuid,
                    RepricingRule.is_active == True,
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                return [
                    {
                        "strategy": r.strategy.value,
                        "min_price_multiplier": float(r.min_price_multiplier),
                        "max_price_multiplier": float(r.max_price_multiplier),
                        "target_margin_pct": float(r.target_margin_pct) if r.target_margin_pct else None,
                        "undercut_by_pct": float(r.undercut_by_pct) if r.undercut_by_pct else 0.5,
                        "applies_to_skus": r.applies_to_skus,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("Could not load repricing rules from DB: %s - using defaults", e)
            return [self._default_rule()]

    def _default_rule(self) -> dict:
        return {
            "strategy": "competitive_buybox",
            "min_price_multiplier": 0.9,
            "max_price_multiplier": 1.2,
            "target_margin_pct": None,
            "undercut_by_pct": 0.5,
            "applies_to_skus": None,
        }

    def _match_rule(self, rules: list[dict], sku: Optional[str]) -> Optional[dict]:
        if not rules:
            return self._default_rule()
        for rule in rules:
            skus = rule.get("applies_to_skus")
            if not skus or (sku and sku in skus):
                return rule
        return rules[0] if rules else self._default_rule()

    async def _fetch_competitor_prices(
        self, bridge, credentials: dict, product, variant
    ) -> list[CompetitorPrice]:
        try:
            provider_obj = bridge.shared_provider_list.get(self.provider.lower())
            if provider_obj and hasattr(provider_obj, "get_competitor_prices"):
                identifier = variant.sku or product.external_id
                return await provider_obj.get_competitor_prices(credentials, identifier)
        except Exception as e:
            logger.debug("Could not fetch competitor prices: %s", e)
        return []

    async def _persist_history(
        self, recommendations: list[PriceRecommendation], applied: bool
    ) -> None:
        try:
            from db.postgres import db_session
            from db.models.commerce import PriceHistory
            import uuid

            store_uuid = await self._resolve_store_uuid()
            if not store_uuid:
                return

            async with db_session() as session:
                for rec in recommendations:
                    if rec.recommended_price == rec.current_price:
                        continue
                    session.add(PriceHistory(
                        store_id=store_uuid,
                        product_id=uuid.UUID(rec.product_id) if len(rec.product_id) == 36 else uuid.uuid4(),
                        variant_external_id=rec.variant_id,
                        old_price=rec.current_price,
                        new_price=rec.recommended_price,
                        strategy=rec.strategy,
                        reason=rec.reason,
                        competitor_prices=[c.model_dump(mode="json") for c in rec.competitor_prices],
                        applied_by="system" if applied else "dry_run",
                    ))
        except Exception as e:
            logger.warning("Could not persist price history: %s", e)
