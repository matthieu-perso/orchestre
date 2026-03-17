"""
Carrier integration layer.

Two backends are supported:

1. EasyPost — for Shopify (self-fulfilled) orders.
   Aggregates 100+ carriers: FedEx, UPS, USPS, DHL, Canada Post, etc.
   Single API for rate-shopping and label creation.
   Docs: https://www.easypost.com/docs/api

2. Amazon Buy Shipping — for Amazon MFN (merchant-fulfilled) orders.
   You must buy the label through Amazon to keep your ODR (order defect rate)
   protected and get Buy Shipping Benefits (A-to-z claim protection).
   Docs: https://developer-docs.amazon.com/sp-api/docs/shipping-v2

For Amazon FBA orders, nothing is needed — Amazon handles it automatically.

Rate selection strategy (configurable per fulfillment rule):
  "cheapest"  → lowest cost rate
  "fastest"   → lowest transit_days
  "overnight" → carrier service explicitly containing "overnight" / "next_day"
  "balanced"  → cheapest among services with transit_days <= 3
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

EASYPOST_BASE = "https://api.easypost.com/v2"
AMAZON_SHIPPING_V2 = "https://sellingpartnerapi-na.amazon.com/shipping/v2"


@dataclass
class ShipmentRate:
    rate_id: str
    carrier: str
    service: str
    rate: float
    currency: str
    transit_days: Optional[int]
    delivery_date: Optional[str]


@dataclass
class PurchasedLabel:
    tracking_number: str
    label_url: str
    carrier: str
    service: str
    rate: float
    currency: str


# ---------------------------------------------------------------------------
# EasyPost client (Shopify / any self-fulfilled order)
# ---------------------------------------------------------------------------

class EasyPostClient:
    """
    Thin async wrapper around EasyPost REST API v2.
    EasyPost uses HTTP Basic auth: API key as username, empty password.
    """

    def __init__(self) -> None:
        self.api_key = settings.EASYPOST_API_KEY or ""

    def _auth(self):
        return (self.api_key, "")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=EASYPOST_BASE,
            auth=self._auth(),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

    async def get_rates(
        self,
        to_address: dict,
        from_address: dict,
        parcel: dict,
    ) -> list[ShipmentRate]:
        """
        Create a shipment and return all available rates without buying.

        to_address / from_address format:
          {name, company, street1, street2, city, state, zip, country, phone, email}

        parcel format:
          {length, width, height, weight}   (inches + ounces for US carriers)
        """
        payload = {
            "shipment": {
                "to_address": to_address,
                "from_address": from_address,
                "parcel": parcel,
            }
        }
        async with self._client() as client:
            resp = await client.post("/shipments", json=payload)
            resp.raise_for_status()
            data = resp.json()

        shipment_id = data["id"]
        rates = []
        for r in data.get("rates", []):
            rates.append(ShipmentRate(
                rate_id=r["id"],
                carrier=r["carrier"],
                service=r["service"],
                rate=float(r["rate"]),
                currency=r["currency"],
                transit_days=r.get("delivery_days"),
                delivery_date=r.get("est_delivery_date"),
            ))

        # Store shipment_id on each rate so we can buy later
        for r in rates:
            r.__dict__["_shipment_id"] = shipment_id

        return rates

    async def buy_label(
        self,
        shipment_id: str,
        rate_id: str,
    ) -> PurchasedLabel:
        """Purchase a specific rate and return the shipping label."""
        async with self._client() as client:
            resp = await client.post(
                f"/shipments/{shipment_id}/buy",
                json={"rate": {"id": rate_id}},
            )
            resp.raise_for_status()
            data = resp.json()

        selected = data.get("selected_rate", {})
        label = data.get("postage_label", {})
        tracking = data.get("tracking_code", "")

        return PurchasedLabel(
            tracking_number=tracking,
            label_url=label.get("label_url", ""),
            carrier=selected.get("carrier", ""),
            service=selected.get("service", ""),
            rate=float(selected.get("rate", 0)),
            currency=selected.get("currency", "USD"),
        )

    async def create_and_buy(
        self,
        to_address: dict,
        from_address: dict,
        parcel: dict,
        strategy: str = "cheapest",
    ) -> PurchasedLabel:
        """
        One-shot: get rates, pick the best per strategy, buy, return label.
        This is the main method called by the fulfillment engine.
        """
        rates = await self.get_rates(to_address, from_address, parcel)
        if not rates:
            raise ValueError("No shipping rates available for this shipment")

        best = _select_rate(rates, strategy)
        shipment_id = best.__dict__.get("_shipment_id", "")
        if not shipment_id:
            raise ValueError("Shipment ID missing from rate object")

        return await self.buy_label(shipment_id, best.rate_id)

    async def get_tracking(self, tracking_code: str) -> dict:
        """Get tracking status for a shipment."""
        async with self._client() as client:
            resp = await client.get(f"/trackers?tracking_code={tracking_code}")
            resp.raise_for_status()
            trackers = resp.json().get("trackers", [])
            return trackers[0] if trackers else {}


# ---------------------------------------------------------------------------
# Amazon Buy Shipping client (Amazon MFN orders)
# ---------------------------------------------------------------------------

class AmazonBuyShippingClient:
    """
    Wraps Amazon Shipping v2 API for merchant-fulfilled orders.

    Key rule: for MFN orders, always buy the label through Amazon Buy Shipping.
    This provides:
      - A-to-z Guarantee claim protection
      - Valid tracking automatically uploaded to Amazon
      - Lower carrier rates via Amazon's negotiated discounts
    """

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=AMAZON_SHIPPING_V2,
            headers={
                "x-amz-access-token": self.access_token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def get_rates(
        self,
        ship_to: dict,
        ship_from: dict,
        packages: list[dict],
        order_id: str,
        items: list[dict],
    ) -> list[ShipmentRate]:
        """
        Fetch available rates for an MFN order.

        packages: [{weight: {value, unit}, dimensions: {length, width, height, unit}}]
        items: [{asin, title, quantity, unit_price: {amount, currencyCode}}]
        """
        payload = {
            "shipTo": ship_to,
            "shipFrom": ship_from,
            "returnTo": ship_from,
            "packages": packages,
            "channelDetails": {
                "channelType": "AMAZON",
                "amazonOrderDetails": {"orderId": order_id},
            },
            "lineItems": items,
        }
        async with self._client() as client:
            resp = await client.post("/rates", json=payload)
            resp.raise_for_status()
            data = resp.json()

        rates = []
        for offer in data.get("payload", {}).get("rates", []):
            rates.append(ShipmentRate(
                rate_id=offer["rateId"],
                carrier=offer.get("carrier", {}).get("name", ""),
                service=offer.get("service", {}).get("name", ""),
                rate=float(offer.get("totalCharge", {}).get("value", 0)),
                currency=offer.get("totalCharge", {}).get("unit", "USD"),
                transit_days=offer.get("promise", {}).get("deliveryWindow", {}).get("end", None),
                delivery_date=None,
            ))
        return rates

    async def purchase_shipment(
        self,
        rate_id: str,
        packages: list[dict],
        ship_to: dict,
        ship_from: dict,
        order_id: str,
        items: list[dict],
    ) -> PurchasedLabel:
        """Purchase a label for an MFN order."""
        payload = {
            "rateId": rate_id,
            "requestedDocumentSpecification": {
                "format": "PDF",
                "size": {"width": 4, "length": 6, "unit": "INCH"},
                "dpi": 203,
                "pageLayout": "DEFAULT",
                "needFileJoining": False,
                "requestedDocumentTypes": ["LABEL"],
            },
            "additionalInputs": {},
            "shipTo": ship_to,
            "shipFrom": ship_from,
            "returnTo": ship_from,
            "packages": packages,
            "channelDetails": {
                "channelType": "AMAZON",
                "amazonOrderDetails": {"orderId": order_id},
            },
            "lineItems": items,
        }
        async with self._client() as client:
            resp = await client.post("/shipments", json=payload)
            resp.raise_for_status()
            data = resp.json().get("payload", {})

        pkg = (data.get("packageDocumentDetailList") or [{}])[0]
        tracking = pkg.get("trackingId", "")
        label_url = ""

        doc_list = pkg.get("packageDocuments", [{}])
        if doc_list:
            label_url = doc_list[0].get("contents", "")

        return PurchasedLabel(
            tracking_number=tracking,
            label_url=label_url,
            carrier="Amazon Buy Shipping",
            service=rate_id,
            rate=0.0,
            currency="USD",
        )


# ---------------------------------------------------------------------------
# Rate selection helpers
# ---------------------------------------------------------------------------

def _select_rate(rates: list[ShipmentRate], strategy: str) -> ShipmentRate:
    if not rates:
        raise ValueError("No rates to select from")

    if strategy == "cheapest":
        return min(rates, key=lambda r: r.rate)

    if strategy == "fastest":
        # Prefer rates with known transit_days, fallback to cheapest
        with_days = [r for r in rates if r.transit_days is not None]
        if with_days:
            return min(with_days, key=lambda r: (r.transit_days, r.rate))
        return min(rates, key=lambda r: r.rate)

    if strategy == "overnight":
        overnight_keywords = ("overnight", "next_day", "next day", "priority_overnight",
                              "first_overnight", "priority express")
        overnight = [
            r for r in rates
            if any(kw in r.service.lower() for kw in overnight_keywords)
        ]
        if overnight:
            return min(overnight, key=lambda r: r.rate)
        # Fallback: fastest available
        return _select_rate(rates, "fastest")

    if strategy == "balanced":
        # Cheapest among services with transit_days <= 3
        fast_enough = [r for r in rates if r.transit_days is not None and r.transit_days <= 3]
        if fast_enough:
            return min(fast_enough, key=lambda r: r.rate)
        return _select_rate(rates, "cheapest")

    return _select_rate(rates, "cheapest")


# Singletons — shared across processes
easypost_client = EasyPostClient()
