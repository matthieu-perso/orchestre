"""
Inventory Restock Engine.

Workflow:
1. Fetch current inventory levels from provider
2. Fetch recent order history to calculate sales velocity
3. Apply forecasting model (simple moving average, extensible to ML)
4. Calculate days-of-stock remaining
5. Compare against reorder points + lead times
6. Generate RestockRecommendation for each at-risk SKU
7. Optionally create draft purchase orders

Key formulas:
  - Sales velocity = units sold / days in window
  - Days of stock = current_qty / sales_velocity
  - Reorder point = lead_time_days * velocity * safety_factor
  - EOQ = sqrt(2 * annual_demand * order_cost / holding_cost_rate)
"""
import logging
import math
from decimal import Decimal
from typing import Optional

from schemas.commerce import InventoryLevel, OrderListOptions, RestockRecommendation

logger = logging.getLogger(__name__)

SAFETY_STOCK_FACTOR = 1.5       # 50% buffer above lead-time demand
DEFAULT_LEAD_TIME_DAYS = 7
ANALYSIS_WINDOW_DAYS = 30       # Sales velocity over 30 days
ORDER_COST = Decimal("50")      # Estimated cost to place one PO (admin)
HOLDING_COST_RATE = Decimal("0.25")  # Annual holding cost as % of unit cost


class RestockEngine:
    def __init__(self, store_id: str, user_id: str, provider: str) -> None:
        # store_id = identifier_name (e.g. "mystore.myshopify.com")
        self.store_id = store_id
        self.user_id = user_id
        self.provider = provider

    async def _resolve_store_uuid(self):
        try:
            from db.cruds.stores import get_store
            store = await get_store(self.user_id, self.provider, self.store_id)
            return store.id if store else None
        except Exception:
            return None

    async def run(self, dry_run: bool = False) -> dict:
        from db.cruds.users import get_user_data
        from providers.bridge import bridge

        user_data = get_user_data(self.user_id)
        credentials = (user_data or {}).get(self.provider, {}).get(self.store_id, {})

        inventory = await self._fetch_inventory(bridge, credentials)
        velocity_map = await self._calculate_velocity(bridge, credentials)
        products = await self._fetch_product_costs(bridge, credentials)

        recommendations: list[RestockRecommendation] = []
        for level in inventory:
            sku = level.sku or level.inventory_item_id
            velocity = velocity_map.get(sku, 0.0)

            if velocity <= 0:
                continue

            current_stock = level.available
            incoming = level.incoming or 0
            effective_stock = current_stock + incoming

            lead_time = self._get_lead_time(sku)
            reorder_point = math.ceil(velocity * lead_time * SAFETY_STOCK_FACTOR)
            days_remaining = effective_stock / velocity if velocity > 0 else float("inf")

            if effective_stock > reorder_point:
                continue  # Adequate stock

            order_qty = self._eoq(
                annual_demand=velocity * 365,
                unit_cost=products.get(sku, Decimal("10")),
            )
            order_qty = max(order_qty, reorder_point - effective_stock)

            urgency = "normal"
            if days_remaining <= lead_time:
                urgency = "critical"
            elif days_remaining <= lead_time * 1.5:
                urgency = "high"

            cost = products.get(sku)
            estimated_cost = (cost * order_qty) if cost else None

            recommendations.append(RestockRecommendation(
                sku=sku,
                product_title=sku,
                current_stock=current_stock,
                days_of_stock_remaining=round(days_remaining, 1),
                avg_daily_sales=round(velocity, 2),
                reorder_point=reorder_point,
                recommended_order_qty=order_qty,
                estimated_cost=estimated_cost,
                urgency=urgency,
            ))

        # Sort by urgency
        urgency_order = {"critical": 0, "high": 1, "normal": 2}
        recommendations.sort(key=lambda r: urgency_order.get(r.urgency, 3))

        if not dry_run and recommendations:
            await self._persist_recommendations(recommendations)

        return {
            "recommendations": [r.model_dump() for r in recommendations],
            "critical_count": sum(1 for r in recommendations if r.urgency == "critical"),
            "high_count": sum(1 for r in recommendations if r.urgency == "high"),
            "total": len(recommendations),
            "dry_run": dry_run,
        }

    # ------------------------------------------------------------------
    # EOQ calculation
    # ------------------------------------------------------------------

    def _eoq(self, annual_demand: float, unit_cost: Decimal) -> int:
        """Economic Order Quantity formula."""
        if annual_demand <= 0 or unit_cost <= 0:
            return 1
        holding = float(unit_cost) * float(HOLDING_COST_RATE)
        eoq = math.sqrt((2 * annual_demand * float(ORDER_COST)) / holding)
        return max(1, math.ceil(eoq))

    def _get_lead_time(self, sku: str) -> int:
        """Get lead time for SKU. Extend to load from DB."""
        return DEFAULT_LEAD_TIME_DAYS

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    async def _fetch_inventory(self, bridge, credentials: dict) -> list[InventoryLevel]:
        try:
            provider_obj = bridge.shared_provider_list.get(self.provider.lower())
            if provider_obj and hasattr(provider_obj, "get_inventory_levels"):
                return await provider_obj.get_inventory_levels(credentials)
        except Exception as e:
            logger.warning("Could not fetch inventory: %s", e)
        return []

    async def _calculate_velocity(self, bridge, credentials: dict) -> dict[str, float]:
        """Returns {sku: avg_units_sold_per_day} over ANALYSIS_WINDOW_DAYS."""
        try:
            provider_obj = bridge.shared_provider_list.get(self.provider.lower())
            if not (provider_obj and hasattr(provider_obj, "get_orders")):
                return {}

            options = OrderListOptions(
                since_hours=ANALYSIS_WINDOW_DAYS * 24,
                status="any",
                fulfillment_status="fulfilled",
            )
            orders = await provider_obj.get_orders(credentials, options)

            sku_units: dict[str, int] = {}
            for order in orders:
                for item in (order.line_items or []):
                    sku = item.sku or item.external_id
                    sku_units[sku] = sku_units.get(sku, 0) + item.quantity

            return {sku: units / ANALYSIS_WINDOW_DAYS for sku, units in sku_units.items()}
        except Exception as e:
            logger.warning("Could not calculate velocity: %s", e)
            return {}

    async def _fetch_product_costs(self, bridge, credentials: dict) -> dict[str, Decimal]:
        """Returns {sku: cost_per_item}."""
        try:
            provider_obj = bridge.shared_provider_list.get(self.provider.lower())
            if not (provider_obj and hasattr(provider_obj, "get_all_products")):
                return {}
            from schemas.commerce import ProductListOptions
            products = await provider_obj.get_all_products(credentials, ProductListOptions())
            costs: dict[str, Decimal] = {}
            for product in products:
                for variant in (product.variants or []):
                    if variant.sku and variant.cost_per_item:
                        costs[variant.sku] = variant.cost_per_item
            return costs
        except Exception as e:
            logger.warning("Could not fetch product costs: %s", e)
            return {}

    async def _persist_recommendations(
        self, recommendations: list[RestockRecommendation]
    ) -> None:
        """Persist to DB and fire alerts for critical/warning SKUs."""
        try:
            from db.postgres import db_session
            from db.models.commerce import AutomationRun

            store_uuid = await self._resolve_store_uuid()
            if not store_uuid:
                return

            async with db_session() as session:
                session.add(AutomationRun(
                    store_id=store_uuid,
                    job_name="inventory_restock",
                    status="completed",
                    result={
                        "recommendations": [r.model_dump() for r in recommendations],
                        "critical": sum(1 for r in recommendations if r.urgency == "critical"),
                    },
                ))
        except Exception as e:
            logger.warning("Could not persist restock run: %s", e)

        # Fire alerts for critical and warning SKUs
        await self._alert_restock(recommendations)

    async def _alert_restock(self, recommendations: list[RestockRecommendation]) -> None:
        try:
            from services.notifications.notifier import AlertLevel, notifier

            critical = [r for r in recommendations if r.urgency == "critical"]
            warning = [r for r in recommendations if r.urgency == "warning"]

            if critical:
                lines = "\n".join(
                    f"• {r.sku}: {r.current_stock} units left ({r.days_of_stock:.0f} days). "
                    f"Reorder {r.recommended_order_qty} units."
                    for r in critical[:10]  # cap at 10 to avoid message overflow
                )
                await notifier.send(
                    title=f"🔴 Critical restock: {len(critical)} SKU(s) nearly out of stock",
                    message=lines,
                    level=AlertLevel.ERROR,
                    store_id=self.store_id,
                    details={
                        "Critical SKUs": len(critical),
                        "Warning SKUs": len(warning),
                        "Store": self.store_id,
                    },
                )

            elif warning:
                lines = "\n".join(
                    f"• {r.sku}: {r.days_of_stock:.0f} days of stock. "
                    f"Reorder {r.recommended_order_qty} units."
                    for r in warning[:10]
                )
                await notifier.send(
                    title=f"⚠️ Restock reminder: {len(warning)} SKU(s) approaching reorder point",
                    message=lines,
                    level=AlertLevel.WARNING,
                    store_id=self.store_id,
                    details={
                        "Warning SKUs": len(warning),
                        "Store": self.store_id,
                    },
                )
        except Exception as e:
            logger.warning("Could not send restock alert: %s", e)
