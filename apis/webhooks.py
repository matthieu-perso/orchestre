"""
Webhook endpoints for Shopify and Amazon.

Shopify webhooks are HMAC-verified, then dispatched to ARQ jobs.
Amazon SNS notifications follow the same pattern.

Webhook topics auto-registered by ShopifyProvider on store connection.
"""
import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from core.config import settings
from core.utils.log import BackLog
from core.utils.message import MessageErr, MessageOK

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def _verify_shopify_hmac(body: bytes, hmac_header: str) -> bool:
    secret = settings.SHOPIFY_WEBHOOK_SECRET or settings.SHOPIFY_API_SECRET or ""
    if not secret:
        if settings.STRICT_WEBHOOK_VERIFICATION:
            logger.error("Shopify webhook secret not configured - rejecting (STRICT_WEBHOOK_VERIFICATION)")
            return False
        logger.warning("Shopify webhook secret not configured - skipping verification")
        return True
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, hmac_header or "")


def _build_sns_string_to_sign(msg: dict) -> str:
    """Build the canonical string to sign for SNS message verification."""
    excluded = {"Signature", "SignatureVersion"}
    parts = []
    for key in sorted(k for k in msg if k not in excluded):
        val = msg.get(key, "")
        if val is None:
            val = ""
        parts.append(f"{key}\n{val}\n")
    return "".join(parts)


def _verify_amazon_sns_signature(body: dict) -> bool:
    """
    Verify SNS message signature per AWS docs.
    Fetches cert from SigningCertURL (must be AWS), verifies RSA signature.
    """
    cert_url = body.get("SigningCertURL", "")
    if not cert_url:
        return False
    parsed = urlparse(cert_url)
    if parsed.scheme != "https":
        return False
    if "amazonaws.com" not in parsed.netloc and "amazon.com" not in parsed.netloc:
        return False

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(cert_url)
            resp.raise_for_status()
            cert_pem = resp.content
    except Exception as e:
        logger.warning("SNS cert fetch failed: %s", e)
        return False

    try:
        x509 = load_pem_x509_certificate(cert_pem)
        public_key = x509.public_key()
    except Exception as e:
        logger.warning("SNS cert load failed: %s", e)
        return False

    signature_b64 = body.get("Signature", "")
    if not signature_b64:
        return False
    try:
        signature = base64.b64decode(signature_b64)
    except Exception:
        return False

    string_to_sign = _build_sns_string_to_sign(body)
    sig_version = body.get("SignatureVersion", "1")
    hash_algo = hashes.SHA1() if sig_version == "1" else hashes.SHA256()

    try:
        public_key.verify(signature, string_to_sign.encode(), padding.PKCS1v15(), hash_algo)
        return True
    except Exception as e:
        logger.warning("SNS signature verification failed: %s", e)
        return False


def _verify_amazon_sns(body: dict) -> bool:
    """Verify SNS message. When STRICT_WEBHOOK_VERIFICATION, requires signature verification."""
    msg_type = body.get("Type", "")
    if msg_type not in ("Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"):
        return False
    if settings.STRICT_WEBHOOK_VERIFICATION:
        return _verify_amazon_sns_signature(body)
    return True


# ---------------------------------------------------------------------------
# Shopify webhooks
# ---------------------------------------------------------------------------

@router.post(
    "/shopify/{topic}",
    summary="Shopify webhook receiver",
    description="Receives all Shopify webhook events. Topic maps to order/product/inventory events.",
)
async def shopify_webhook(
    topic: str,
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    x_shopify_shop_domain: Optional[str] = Header(None),
    x_shopify_topic: Optional[str] = Header(None),
):
    body = await request.body()

    if not _verify_shopify_hmac(body, x_shopify_hmac_sha256 or ""):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    shop_domain = x_shopify_shop_domain or ""
    actual_topic = x_shopify_topic or topic.replace("_", "/")

    logger.info("Shopify webhook: topic=%s shop=%s", actual_topic, shop_domain)

    await _dispatch_shopify_event(actual_topic, shop_domain, payload)

    return {"ok": True}


async def _resolve_user_from_shop(shop_domain: str) -> tuple[str, str]:
    """Return (user_id, identifier_name) for a Shopify shop domain."""
    try:
        from db.cruds.stores import get_store_by_domain
        store = await get_store_by_domain(shop_domain)
        if store:
            return store.user_id, store.identifier
    except Exception as e:
        logger.warning("Could not resolve user for shop %s: %s", shop_domain, e)
    return "", shop_domain


async def _dispatch_shopify_event(
    topic: str, shop_domain: str, payload: dict
) -> None:
    """Map Shopify topics to ARQ jobs."""
    from arq import create_pool
    from core.queue.worker import get_redis_settings

    pool = await create_pool(get_redis_settings())
    try:
        user_id, identifier_name = await _resolve_user_from_shop(shop_domain)
        store_id = identifier_name  # always use identifier_name as store_id key

        if not user_id:
            logger.warning(
                "Received webhook for unregistered shop %s - topic %s ignored",
                shop_domain, topic,
            )
            return

        if topic in ("orders/create", "orders/updated"):
            note = payload.get("note", "")
            if note:  # Only trigger CS if there's a customer message
                await pool.enqueue_job(
                    "handle_customer_support",
                    store_id=store_id,
                    user_id=user_id,
                    provider="shopifyprovider",
                    order_id=str(payload.get("id", "")),
                    message_content=note,
                    channel="order_webhook",
                )

        elif topic == "orders/fulfilled":
            logger.info("Order fulfilled: %s", payload.get("id"))

        elif topic == "orders/cancelled":
            logger.info("Order cancelled: %s", payload.get("id"))

        elif topic in ("products/create", "products/update"):
            logger.info("Product event %s: %s", topic, payload.get("id"))

        elif topic == "inventory_levels/update":
            logger.info(
                "Inventory update: item=%s location=%s available=%s",
                payload.get("inventory_item_id"),
                payload.get("location_id"),
                payload.get("available"),
            )

        elif topic == "app/uninstalled":
            logger.warning("App uninstalled from shop: %s", shop_domain)

    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# Amazon SNS / SP-API webhooks
# ---------------------------------------------------------------------------

@router.post(
    "/amazon/notifications",
    summary="Amazon SP-API notification receiver",
    description="Receives Amazon SNS notifications for order and inventory events.",
)
async def amazon_notification(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not _verify_amazon_sns(payload):
        raise HTTPException(status_code=401, detail="Invalid SNS notification")

    msg_type = payload.get("Type", "")

    if msg_type == "SubscriptionConfirmation":
        # Auto-confirm SNS subscription
        subscribe_url = payload.get("SubscribeURL", "")
        if subscribe_url:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.get(subscribe_url)
        return {"confirmed": True}

    try:
        message = json.loads(payload.get("Message", "{}"))
        notif_type = message.get("NotificationType", "")
        payload_data = message.get("Payload", {})

        logger.info("Amazon notification: type=%s", notif_type)

        await _dispatch_amazon_event(notif_type, payload_data)

    except Exception as e:
        logger.exception("Amazon notification processing failed: %s", e)

    return {"ok": True}


async def _dispatch_amazon_event(notif_type: str, payload: dict) -> None:
    from arq import create_pool
    from core.queue.worker import get_redis_settings

    pool = await create_pool(get_redis_settings())
    try:
        if notif_type == "ORDER_CHANGE":
            order_id = payload.get("OrderChangeNotification", {}).get("AmazonOrderId", "")
            logger.info("Amazon order change: %s", order_id)

        elif notif_type == "BRANDED_ITEM_CONTENT_CHANGE":
            logger.info("Amazon content change notification")

        elif notif_type == "ITEM_PRODUCT_TYPE_CHANGE":
            logger.info("Amazon product type change")

        elif notif_type == "FBA_INVENTORY_AVAILABILITY_CHANGES":
            logger.info("Amazon FBA inventory change")

    finally:
        await pool.aclose()
