"""
Notification service — Slack + email alerts.

Single interface: await notifier.send(...)
Both channels are optional and independently configurable via env vars.
If neither is configured, alerts are logged only (never silently dropped).

Covers:
  - Restock recommendations
  - Fulfillment actions (carrier, cost, tracking number)
  - Background job failures
  - Anomaly alerts (ACOS spike, stock-out, etc.)
"""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# Slack color sidebar per level
_SLACK_COLORS = {
    AlertLevel.INFO: "#36a64f",      # green
    AlertLevel.WARNING: "#ff9500",   # orange
    AlertLevel.ERROR: "#d9534f",     # red
}

_LEVEL_EMOJI = {
    AlertLevel.INFO: "✅",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.ERROR: "🔴",
}


class Notifier:
    """
    Async notification dispatcher.
    Each `send()` call fires all configured channels concurrently.
    Never raises — failures are logged and swallowed so automation continues.
    """

    async def send(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        store_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Send an alert to all configured channels."""
        context = f"[{store_id}] " if store_id else ""
        full_title = f"{context}{title}"

        logger.info("ALERT %s: %s — %s", level.upper(), full_title, message)

        tasks = []
        if settings.SLACK_WEBHOOK_URL:
            tasks.append(self._send_slack(full_title, message, level, details))
        if settings.ALERT_EMAIL_TO and settings.SMTP_HOST:
            tasks.append(self._send_email(full_title, message, level, details))

        if not tasks:
            return  # logged above, nothing else configured

        import asyncio
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Notification delivery failed (non-fatal): %s", r)

    # -------------------------------------------------------------------------
    # Slack
    # -------------------------------------------------------------------------

    async def _send_slack(
        self,
        title: str,
        message: str,
        level: AlertLevel,
        details: Optional[dict],
    ) -> None:
        emoji = _LEVEL_EMOJI[level]
        color = _SLACK_COLORS[level]

        fields = []
        if details:
            for k, v in details.items():
                fields.append({"title": str(k), "value": str(v), "short": True})

        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"{emoji} {title}",
                    "text": message,
                    "fields": fields,
                    "footer": "Orchestre Autopilot",
                    "ts": __import__("time").time(),
                }
            ]
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.SLACK_WEBHOOK_URL, json=payload)
            resp.raise_for_status()

    # -------------------------------------------------------------------------
    # Email (SMTP)
    # -------------------------------------------------------------------------

    async def _send_email(
        self,
        title: str,
        message: str,
        level: AlertLevel,
        details: Optional[dict],
    ) -> None:
        import asyncio

        def _sync_send():
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Orchestre {level.upper()}] {title}"
            msg["From"] = settings.SMTP_FROM_EMAIL or settings.ALERT_EMAIL_TO
            msg["To"] = settings.ALERT_EMAIL_TO

            details_html = ""
            if details:
                rows = "".join(
                    f"<tr><td style='padding:4px 8px;font-weight:bold'>{k}</td>"
                    f"<td style='padding:4px 8px'>{v}</td></tr>"
                    for k, v in details.items()
                )
                details_html = f"<table style='border-collapse:collapse;margin-top:12px'>{rows}</table>"

            level_colors = {
                AlertLevel.INFO: "#36a64f",
                AlertLevel.WARNING: "#ff9500",
                AlertLevel.ERROR: "#d9534f",
            }
            color = level_colors[level]

            html = f"""
            <html><body style="font-family:Arial,sans-serif;color:#333">
              <div style="border-left:4px solid {color};padding:12px 16px;background:#f9f9f9">
                <h2 style="margin:0 0 8px">{title}</h2>
                <p style="margin:0">{message}</p>
                {details_html}
              </div>
              <p style="color:#999;font-size:11px;margin-top:16px">Orchestre Autopilot</p>
            </body></html>
            """

            text = f"{title}\n\n{message}"
            if details:
                text += "\n\n" + "\n".join(f"{k}: {v}" for k, v in details.items())

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            context = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                if settings.SMTP_USE_TLS:
                    server.starttls(context=context)
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
                server.sendmail(
                    msg["From"],
                    [settings.ALERT_EMAIL_TO],
                    msg.as_string(),
                )

        await asyncio.get_event_loop().run_in_executor(None, _sync_send)


# Singleton
notifier = Notifier()
