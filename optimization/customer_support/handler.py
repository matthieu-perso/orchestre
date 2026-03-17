"""
AI Customer Support Handler.

Capabilities:
- Intent classification (order status / refund / product question / complaint / review)
- Sentiment analysis
- Context enrichment (fetch order data, product info)
- AI response generation with persona + brand guidelines
- Escalation routing (human agent, manager)
- Review response automation (Amazon, Shopify)
- Multi-channel: email, chat, marketplace messages

The LLM prompt is structured to:
1. Understand the issue
2. Look up order/product context
3. Generate a specific, helpful response (not generic boilerplate)
4. Decide if escalation is needed
"""
import json
import logging
from typing import Any, Optional

from core.config import settings
from schemas.commerce import SupportMessage, SupportResponse

logger = logging.getLogger(__name__)

SUPPORT_CATEGORIES = [
    "order_status",
    "shipping_inquiry",
    "return_request",
    "refund_request",
    "product_question",
    "complaint",
    "compliment",
    "review_response",
    "other",
]

ESCALATION_TRIGGERS = [
    "legal", "lawsuit", "attorney", "fraud", "stolen", "threatening",
    "discrimination", "safety", "injury", "recall",
]


class CustomerSupportHandler:
    def __init__(self, store_id: str, user_id: str, provider: str) -> None:
        self.store_id = store_id
        self.user_id = user_id
        self.provider = provider

    async def handle(
        self,
        order_id: Optional[str] = None,
        message_id: Optional[str] = None,
        message_content: str = "",
        channel: str = "email",
    ) -> SupportResponse:
        # 1. Check for hard escalation triggers
        if self._needs_immediate_escalation(message_content):
            return SupportResponse(
                message_id=message_id,
                response_text="",
                category="escalation",
                sentiment="negative",
                confidence=1.0,
                should_escalate=True,
                escalation_reason="Hard escalation trigger detected in message",
            )

        # 2. Fetch order context if available
        order_context = ""
        if order_id:
            order_context = await self._fetch_order_context(order_id)

        # 3. Generate AI response with context
        response_data = await self._generate_response(
            message_content=message_content,
            order_context=order_context,
            channel=channel,
        )

        # 4. Persist + optionally auto-send
        response = SupportResponse(
            message_id=message_id,
            response_text=response_data.get("response", ""),
            category=response_data.get("category", "other"),
            sentiment=response_data.get("sentiment", "neutral"),
            confidence=float(response_data.get("confidence", 0.8)),
            should_escalate=response_data.get("should_escalate", False),
            escalation_reason=response_data.get("escalation_reason"),
            actions_taken=response_data.get("actions", []),
        )

        await self._log_interaction(message_content, response)
        return response

    # ------------------------------------------------------------------
    # AI response generation
    # ------------------------------------------------------------------

    async def _generate_response(
        self,
        message_content: str,
        order_context: str,
        channel: str,
    ) -> dict:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        system_prompt = f"""You are a professional customer support agent for an e-commerce store.
Your goal is to provide helpful, empathetic, and accurate responses.

Guidelines:
- Be concise and specific (avoid generic boilerplate)
- Always reference the customer's specific situation
- Offer concrete next steps or solutions
- Maintain a friendly but professional tone
- If you cannot fully resolve the issue, explain what you CAN do

You must respond in valid JSON with these fields:
{{
  "response": "The actual response text to send to the customer",
  "category": "One of: {', '.join(SUPPORT_CATEGORIES)}",
  "sentiment": "positive | neutral | negative | angry",
  "confidence": 0.0-1.0 (how confident you are in this response),
  "should_escalate": true/false,
  "escalation_reason": "Reason if should_escalate is true, else null",
  "actions": ["list of any actions taken or recommended, e.g. 'issue_refund', 'send_replacement'"]
}}

Channel: {channel}
{f"Order context: {order_context}" if order_context else ""}"""

        try:
            llm = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                openai_api_key=settings.OPENAI_API_KEY,
                temperature=0.3,
            )
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Customer message:\n{message_content}"),
            ]
            result = await llm.ainvoke(messages)
            content = result.content

            # Parse JSON response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            return json.loads(content)

        except Exception as e:
            logger.exception("LLM customer support generation failed: %s", e)
            return {
                "response": "Thank you for reaching out. We've received your message and our team will get back to you within 24 hours.",
                "category": "other",
                "sentiment": "neutral",
                "confidence": 0.3,
                "should_escalate": True,
                "escalation_reason": f"AI generation failed: {str(e)}",
                "actions": [],
            }

    # ------------------------------------------------------------------
    # Review response automation
    # ------------------------------------------------------------------

    async def generate_review_response(
        self,
        review_text: str,
        rating: int,
        product_name: str = "",
    ) -> str:
        """Generate a professional response to a customer review."""
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        tone = "grateful and enthusiastic" if rating >= 4 else (
            "empathetic and solution-focused" if rating == 3 else
            "apologetic, empathetic, and resolution-focused"
        )

        system = f"""You are responding to a customer review on behalf of the seller.
Tone: {tone}
Rating: {rating}/5 stars
Product: {product_name}

Rules:
- Keep response under 150 words
- Be specific to the review content (don't be generic)
- For negative reviews: acknowledge the issue, apologize sincerely, offer resolution
- For positive reviews: thank them specifically, reinforce what they liked
- Never be defensive or dismissive
- Include a call to action for negative reviews (contact us at support@...)
- Do NOT mention competitor brands or make pricing claims

Respond with ONLY the review response text (no JSON, no preamble)."""

        try:
            llm = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                openai_api_key=settings.OPENAI_API_KEY,
                temperature=0.4,
            )
            result = await llm.ainvoke([
                SystemMessage(content=system),
                HumanMessage(content=f"Review:\n{review_text}"),
            ])
            return result.content.strip()
        except Exception as e:
            logger.exception("Review response generation failed: %s", e)
            if rating >= 4:
                return f"Thank you so much for your wonderful review of {product_name}! We're thrilled you love it. Your feedback means the world to us!"
            return f"We sincerely apologize for your experience with {product_name}. Please contact us directly so we can make this right for you."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _needs_immediate_escalation(self, text: str) -> bool:
        text_lower = text.lower()
        return any(trigger in text_lower for trigger in ESCALATION_TRIGGERS)

    async def _fetch_order_context(self, order_id: str) -> str:
        try:
            from providers.bridge import bridge
            from db.cruds.users import get_user_data

            user_data = get_user_data(self.user_id)
            credentials = (user_data or {}).get(self.provider, {}).get(self.store_id, {})
            provider_obj = bridge.shared_provider_list.get(self.provider.lower())

            if provider_obj and hasattr(provider_obj, "get_order"):
                order = await provider_obj.get_order(credentials, order_id)
                if order:
                    items = ", ".join(
                        f"{li.title} x{li.quantity}" for li in order.line_items[:3]
                    )
                    return (
                        f"Order #{order.order_number}: {order.status}, "
                        f"${order.total_price} {order.currency}, "
                        f"Items: {items}, "
                        f"Fulfillment: {order.fulfillment_status or 'pending'}"
                    )
        except Exception as e:
            logger.debug("Could not fetch order context: %s", e)
        return ""

    async def _resolve_store_uuid(self):
        try:
            from db.cruds.stores import get_store
            store = await get_store(self.user_id, self.provider, self.store_id)
            return store.id if store else None
        except Exception:
            return None

    async def _log_interaction(
        self, message: str, response: SupportResponse
    ) -> None:
        try:
            from db.postgres import db_session
            from db.models.commerce import AutomationRun

            store_uuid = await self._resolve_store_uuid()
            if not store_uuid:
                return

            async with db_session() as session:
                session.add(AutomationRun(
                    store_id=store_uuid,
                    job_name="customer_support",
                    status="escalated" if response.should_escalate else "completed",
                    result={
                        "category": response.category,
                        "sentiment": response.sentiment,
                        "confidence": response.confidence,
                        "escalated": response.should_escalate,
                    },
                ))
        except Exception as e:
            logger.debug("Could not log support interaction: %s", e)
