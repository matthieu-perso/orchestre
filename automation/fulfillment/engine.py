"""
Auto-fulfillment engine.

Run every 10 minutes via ARQ cron. For each active store:
  1. Fetch unfulfilled, paid orders from Shopify or Amazon
  2. Evaluate each order against the store's FulfillmentRule list
  3. Execute the first matching rule's action:
     - FULFILL  → create shipping label (EasyPost or Amazon Buy Shipping), then
                  confirm fulfillment in Shopify/Amazon with tracking number
     - SKIP     → log and move on (FBA orders, digital goods)
     - FLAG     → alert operator via Slack/email, do not touch the order
     - HOLD     → mark order as held in FulfillmentLog, skip until rule changes
  4. Write a FulfillmentLog row for every decision (audit trail)
  5. Send a summary notification when the batch finishes

Carrier integration:
  - Shopify (manual/self-fulfilled): EasyPost rate-shops across FedEx/UPS/USPS/DHL
  - Amazon MFN: Amazon Buy Shipping API (required for A-to-z protection)
  - Amazon FBA: automatic SKIP — Amazon handles everything

Each store has a JSON `ship_from` address in its settings field:
  store.settings["ship_from"] = {name, company, street1, city, state, zip, country, phone}
If that's missing, the engine raises a clear error and flags the order.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select

from db.models.commerce import FulfillmentActionType, FulfillmentLog, FulfillmentRule, Store
from db.postgres import db_session
from services.notifications.notifier import AlertLevel, notifier

logger = logging.getLogger(__name__)


@dataclass
class FulfillmentDecision:
    order_id: str
    action: FulfillmentActionType
    rule_name: str
    rule_id: Optional[uuid.UUID]
    tracking_number: Optional[str] = None
    label_url: Optional[str] = None
    carrier: Optional[str] = None
    service: Optional[str] = None
    shipping_cost: Optional[float] = None
    error: Optional[str] = None
    status: str = "pending"  # success | failed | skipped | flagged | held


class FulfillmentEngine:

    def __init__(self, store_id: str, user_id: str, provider: str) -> None:
        self.store_id = store_id
        self.user_id = user_id
        self.provider = provider

    async def run(self, dry_run: bool = False) -> dict:
        """
        Main entry point. Returns a summary dict of decisions made.
        dry_run=True evaluates rules and logs decisions but does NOT create labels
        or confirm fulfillments.
        """
        from db.cruds.stores import get_store
        from db.cruds.users import get_user_data
        from providers.bridge import bridge

        store = await get_store(self.user_id, self.provider, self.store_id)
        if not store:
            raise ValueError(f"Store {self.store_id} not found for user {self.user_id}")

        credentials = (get_user_data(self.user_id) or {}).get(self.provider, {}).get(self.store_id, {})
        if not credentials:
            raise ValueError(f"No credentials found for store {self.store_id}")

        provider_instance = bridge.shared_provider_list.get(self.provider.lower())
        if not provider_instance:
            raise ValueError(f"Provider {self.provider} not loaded")

        rules = await self._load_rules(store.id)

        # Fetch unfulfilled paid orders
        from schemas.commerce import OrderListOptions
        options = OrderListOptions(
            since_hours=24,
            status="any",
            fulfillment_status="unfulfilled",
        )
        try:
            orders = await provider_instance.get_orders(credentials, options)
        except Exception as e:
            logger.exception("Failed to fetch orders for store %s: %s", self.store_id, e)
            await notifier.send(
                title="Fulfillment: Failed to fetch orders",
                message=str(e),
                level=AlertLevel.ERROR,
                store_id=self.store_id,
            )
            return {"status": "error", "error": str(e)}

        decisions: list[FulfillmentDecision] = []
        for order in orders:
            if not self._is_paid(order):
                continue
            if not self._needs_fulfillment(order):
                continue

            decision = await self._process_order(
                order=order,
                rules=rules,
                store=store,
                credentials=credentials,
                provider_instance=provider_instance,
                dry_run=dry_run,
            )
            decisions.append(decision)
            await self._log_decision(store.id, decision)

        summary = self._build_summary(decisions, dry_run)
        await self._notify_summary(summary, dry_run)
        return summary

    # -------------------------------------------------------------------------
    # Per-order processing
    # -------------------------------------------------------------------------

    async def _process_order(
        self,
        order,
        rules: list[FulfillmentRule],
        store: Store,
        credentials: dict,
        provider_instance,
        dry_run: bool,
    ) -> FulfillmentDecision:
        matched_rule = self._match_rule(order, rules)

        if matched_rule is None:
            # No rule matched — flag for manual review
            decision = FulfillmentDecision(
                order_id=order.order_id,
                action=FulfillmentActionType.FLAG,
                rule_name="(no matching rule)",
                rule_id=None,
                status="flagged",
            )
            await notifier.send(
                title="Fulfillment: Order needs manual review",
                message=f"Order {order.order_id} did not match any fulfillment rule.",
                level=AlertLevel.WARNING,
                store_id=self.store_id,
                details={"order_id": order.order_id, "total": str(order.total_price)},
            )
            return decision

        action = matched_rule.action_type

        if action == FulfillmentActionType.SKIP:
            return FulfillmentDecision(
                order_id=order.order_id,
                action=action,
                rule_name=matched_rule.name,
                rule_id=matched_rule.id,
                status="skipped",
            )

        if action == FulfillmentActionType.HOLD:
            return FulfillmentDecision(
                order_id=order.order_id,
                action=action,
                rule_name=matched_rule.name,
                rule_id=matched_rule.id,
                status="held",
            )

        if action == FulfillmentActionType.FLAG:
            await notifier.send(
                title="Fulfillment: Order flagged by rule",
                message=f"Order {order.order_id} matched rule '{matched_rule.name}' which requires manual fulfillment.",
                level=AlertLevel.WARNING,
                store_id=self.store_id,
                details={"order_id": order.order_id, "rule": matched_rule.name},
            )
            return FulfillmentDecision(
                order_id=order.order_id,
                action=action,
                rule_name=matched_rule.name,
                rule_id=matched_rule.id,
                status="flagged",
            )

        # action == FULFILL
        return await self._fulfill_order(
            order=order,
            rule=matched_rule,
            store=store,
            credentials=credentials,
            provider_instance=provider_instance,
            dry_run=dry_run,
        )

    async def _fulfill_order(
        self,
        order,
        rule: FulfillmentRule,
        store: Store,
        credentials: dict,
        provider_instance,
        dry_run: bool,
    ) -> FulfillmentDecision:
        config = rule.action_config or {}
        strategy = config.get("carrier_strategy", "cheapest")

        try:
            if dry_run:
                return FulfillmentDecision(
                    order_id=order.order_id,
                    action=FulfillmentActionType.FULFILL,
                    rule_name=rule.name,
                    rule_id=rule.id,
                    status="dry_run",
                    carrier="(dry_run)",
                )

            if self.provider == "amazonprovider":
                label = await self._fulfill_amazon(order, rule, credentials, strategy)
            else:
                label = await self._fulfill_shopify(order, rule, store, credentials, strategy)

            # Confirm fulfillment in the platform with tracking number
            from schemas.commerce import FulfillmentRequest
            req = FulfillmentRequest(
                order_id=order.order_id,
                tracking_number=label.tracking_number,
                tracking_company=label.carrier,
                notify_customer=config.get("notify_customer", True),
            )
            await provider_instance.fulfill_order(credentials, req)

            await notifier.send(
                title="Order fulfilled",
                message=f"Order {order.order_id} shipped via {label.carrier} {label.service}",
                level=AlertLevel.INFO,
                store_id=self.store_id,
                details={
                    "tracking": label.tracking_number,
                    "carrier": label.carrier,
                    "service": label.service,
                    "cost": f"${label.rate:.2f}",
                },
            )

            return FulfillmentDecision(
                order_id=order.order_id,
                action=FulfillmentActionType.FULFILL,
                rule_name=rule.name,
                rule_id=rule.id,
                tracking_number=label.tracking_number,
                label_url=label.label_url,
                carrier=label.carrier,
                service=label.service,
                shipping_cost=label.rate,
                status="success",
            )

        except Exception as e:
            logger.exception("Fulfillment failed for order %s: %s", order.order_id, e)
            await notifier.send(
                title="Fulfillment failed",
                message=f"Could not fulfill order {order.order_id}: {e}",
                level=AlertLevel.ERROR,
                store_id=self.store_id,
                details={"order_id": order.order_id, "error": str(e)},
            )
            return FulfillmentDecision(
                order_id=order.order_id,
                action=FulfillmentActionType.FULFILL,
                rule_name=rule.name,
                rule_id=rule.id,
                error=str(e),
                status="failed",
            )

    # -------------------------------------------------------------------------
    # Carrier label creation
    # -------------------------------------------------------------------------

    async def _fulfill_shopify(self, order, rule, store, credentials, strategy) -> object:
        """Create label via EasyPost for a Shopify order."""
        from services.fulfillment.carriers import easypost_client

        ship_from = self._get_ship_from(store, rule)
        ship_to = self._order_to_address(order)
        parcel = self._estimate_parcel(order, rule)

        return await easypost_client.create_and_buy(
            to_address=ship_to,
            from_address=ship_from,
            parcel=parcel,
            strategy=strategy,
        )

    async def _fulfill_amazon(self, order, rule, credentials, strategy) -> object:
        """Purchase label via Amazon Buy Shipping API for an MFN order."""
        from providers.bridge import bridge
        from services.fulfillment.carriers import AmazonBuyShippingClient

        amazon_provider = bridge.shared_provider_list.get("amazonprovider")
        access_token = await amazon_provider._credentials_to_token(credentials)
        client = AmazonBuyShippingClient(access_token=access_token)

        config = rule.action_config or {}
        ship_from = config.get("ship_from") or credentials.get("ship_from") or {}

        ship_to = {
            "name": order.shipping_address.name if order.shipping_address else "",
            "addressLine1": order.shipping_address.address1 if order.shipping_address else "",
            "addressLine2": order.shipping_address.address2 if order.shipping_address else "",
            "city": order.shipping_address.city if order.shipping_address else "",
            "stateOrRegion": order.shipping_address.province if order.shipping_address else "",
            "postalCode": order.shipping_address.zip if order.shipping_address else "",
            "countryCode": order.shipping_address.country_code if order.shipping_address else "US",
            "phoneNumber": order.shipping_address.phone if order.shipping_address else "",
        }

        weight_oz = float(config.get("package_weight_oz", 16))
        packages = [{
            "dimensions": {"length": 10, "width": 8, "height": 4, "unit": "INCH"},
            "weight": {"value": weight_oz / 16, "unit": "POUND"},
            "insuredValue": {"value": float(order.total_price or 0), "currencyCode": "USD"},
        }]

        items = [
            {
                "asin": li.sku or "",
                "title": li.title,
                "quantity": li.quantity,
                "unitPrice": {
                    "amount": float(li.price or 0),
                    "currencyCode": "USD",
                },
            }
            for li in (order.line_items or [])
        ]

        rates = await client.get_rates(
            ship_to=ship_to,
            ship_from=ship_from,
            packages=packages,
            order_id=order.order_id,
            items=items,
        )

        from services.fulfillment.carriers import _select_rate
        best_rate = _select_rate(rates, strategy)

        return await client.purchase_shipment(
            rate_id=best_rate.rate_id,
            packages=packages,
            ship_to=ship_to,
            ship_from=ship_from,
            order_id=order.order_id,
            items=items,
        )

    # -------------------------------------------------------------------------
    # Rule matching
    # -------------------------------------------------------------------------

    def _match_rule(
        self, order, rules: list[FulfillmentRule]
    ) -> Optional[FulfillmentRule]:
        for rule in rules:
            if not rule.is_active:
                continue
            if self._rule_matches(order, rule):
                return rule
        return None

    def _rule_matches(self, order, rule: FulfillmentRule) -> bool:
        cond = rule.conditions or {}

        # Payment status
        allowed_payment = cond.get("payment_statuses")
        if allowed_payment:
            if (order.payment_status or "").lower() not in [s.lower() for s in allowed_payment]:
                return False

        # Fulfillment type (fba / manual / digital)
        allowed_types = cond.get("fulfillment_types")
        if allowed_types:
            order_type = self._detect_fulfillment_type(order)
            if order_type not in [t.lower() for t in allowed_types]:
                return False

        # Order value bounds
        total = float(order.total_price or 0)
        min_val = cond.get("min_order_value")
        max_val = cond.get("max_order_value")
        if min_val is not None and total < float(min_val):
            return False
        if max_val is not None and total > float(max_val):
            return False

        # Country code filter
        allowed_countries = cond.get("country_codes")
        excluded_countries = cond.get("exclude_country_codes", [])
        ship_country = ""
        if order.shipping_address:
            ship_country = (order.shipping_address.country_code or "").upper()
        if allowed_countries and ship_country not in [c.upper() for c in allowed_countries]:
            return False
        if excluded_countries and ship_country in [c.upper() for c in excluded_countries]:
            return False

        # Product tags (order must contain a product with ALL required tags)
        required_tags = cond.get("product_tags")
        if required_tags:
            order_tags: set = set()
            for li in (order.line_items or []):
                order_tags.update(t.lower() for t in (li.tags or []))
            if not all(t.lower() in order_tags for t in required_tags):
                return False

        return True

    def _detect_fulfillment_type(self, order) -> str:
        if self.provider == "amazonprovider":
            for li in (order.line_items or []):
                if getattr(li, "fulfillment_channel", "") == "AFN":
                    return "fba"
            return "manual"
        return "manual"

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _is_paid(self, order) -> bool:
        status = (order.payment_status or "").lower()
        return status in ("paid", "authorized", "partially_paid")

    def _needs_fulfillment(self, order) -> bool:
        status = (order.fulfillment_status or "").lower()
        return status in ("", "unfulfilled", "partial", "none")

    def _get_ship_from(self, store: Store, rule: FulfillmentRule) -> dict:
        config = rule.action_config or {}
        ship_from = config.get("ship_from") or (store.settings or {}).get("ship_from")
        if not ship_from:
            raise ValueError(
                f"No ship_from address configured for store {self.store_id}. "
                "Set it in the store settings or fulfillment rule action_config."
            )
        return ship_from

    def _order_to_address(self, order) -> dict:
        addr = order.shipping_address
        if not addr:
            raise ValueError(f"Order {order.order_id} has no shipping address")
        return {
            "name": addr.name or "",
            "company": addr.company or "",
            "street1": addr.address1 or "",
            "street2": addr.address2 or "",
            "city": addr.city or "",
            "state": addr.province_code or addr.province or "",
            "zip": addr.zip or "",
            "country": addr.country_code or "US",
            "phone": addr.phone or "",
        }

    def _estimate_parcel(self, order, rule: FulfillmentRule) -> dict:
        config = rule.action_config or {}
        weight_oz = float(config.get("package_weight_oz", 16))
        return {
            "length": config.get("package_length", 10),
            "width": config.get("package_width", 8),
            "height": config.get("package_height", 4),
            "weight": weight_oz,
        }

    async def _load_rules(self, store_db_id: uuid.UUID) -> list[FulfillmentRule]:
        async with db_session() as session:
            stmt = (
                select(FulfillmentRule)
                .where(
                    FulfillmentRule.store_id == store_db_id,
                    FulfillmentRule.is_active == True,
                )
                .order_by(FulfillmentRule.priority.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def _log_decision(
        self, store_db_id: uuid.UUID, decision: FulfillmentDecision
    ) -> None:
        async with db_session() as session:
            log = FulfillmentLog(
                store_id=store_db_id,
                order_id=decision.order_id,
                rule_id=decision.rule_id,
                action_type=decision.action.value,
                status=decision.status,
                tracking_number=decision.tracking_number,
                label_url=decision.label_url,
                carrier=decision.carrier,
                service=decision.service,
                shipping_cost=Decimal(str(decision.shipping_cost)) if decision.shipping_cost else None,
                error=decision.error,
            )
            session.add(log)

    def _build_summary(self, decisions: list[FulfillmentDecision], dry_run: bool) -> dict:
        fulfilled = [d for d in decisions if d.status == "success"]
        failed = [d for d in decisions if d.status == "failed"]
        skipped = [d for d in decisions if d.status == "skipped"]
        flagged = [d for d in decisions if d.status == "flagged"]
        held = [d for d in decisions if d.status == "held"]
        dry = [d for d in decisions if d.status == "dry_run"]

        total_cost = sum(d.shipping_cost or 0 for d in fulfilled)

        return {
            "store_id": self.store_id,
            "dry_run": dry_run,
            "total_orders": len(decisions),
            "fulfilled": len(fulfilled),
            "failed": len(failed),
            "skipped": len(skipped),
            "flagged": len(flagged),
            "held": len(held),
            "dry_run_count": len(dry),
            "total_shipping_cost": round(total_cost, 2),
            "details": [
                {
                    "order_id": d.order_id,
                    "status": d.status,
                    "carrier": d.carrier,
                    "tracking": d.tracking_number,
                    "cost": d.shipping_cost,
                    "rule": d.rule_name,
                    "error": d.error,
                }
                for d in decisions
            ],
        }

    async def _notify_summary(self, summary: dict, dry_run: bool) -> None:
        if summary["total_orders"] == 0:
            return

        prefix = "[DRY RUN] " if dry_run else ""
        await notifier.send(
            title=f"{prefix}Fulfillment batch complete",
            message=(
                f"{summary['fulfilled']} fulfilled, "
                f"{summary['failed']} failed, "
                f"{summary['flagged']} flagged, "
                f"{summary['skipped']} skipped."
            ),
            level=AlertLevel.ERROR if summary["failed"] > 0 else AlertLevel.INFO,
            store_id=self.store_id,
            details={
                "Total orders processed": summary["total_orders"],
                "Fulfilled": summary["fulfilled"],
                "Failed": summary["failed"],
                "Flagged (manual review)": summary["flagged"],
                "Shipping cost": f"${summary['total_shipping_cost']:.2f}",
            },
        )
