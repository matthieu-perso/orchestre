"""
Commerce agent — GPT-4o with tool calling.

Streaming behavior:
  - Tool calls: execute synchronously, yield a status event so the UI can show
    "Checking your inventory..." while the engine runs
  - Final text: streamed token by token via SSE

Conversation history is stored in Redis (TTL 24h).
user_id is scoped at the session level — the LLM cannot access another user's data.
"""
import json
import logging
from typing import AsyncIterator

import redis.asyncio as aioredis
from openai import AsyncOpenAI

from agents.tools import TOOL_DISPATCH, TOOL_SCHEMAS, execute_tool
from core.config import settings

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 8   # prevent runaway loops
HISTORY_TTL = 86400  # 24h

# Human-readable labels shown while a tool executes
TOOL_STATUS_LABELS: dict[str, str] = {
    "list_stores": "Fetching your stores…",
    "get_orders": "Loading orders…",
    "get_products": "Loading products…",
    "get_inventory": "Checking inventory levels…",
    "get_price_history": "Loading price history…",
    "get_fulfillment_logs": "Loading fulfillment logs…",
    "get_automation_status": "Checking automation status…",
    "list_fulfillment_rules": "Loading fulfillment rules…",
    "run_repricing": "Running repricing engine…",
    "run_inventory_restock": "Analyzing inventory & restock needs…",
    "run_fulfillment": "Running fulfillment engine…",
    "run_amazon_ads": "Optimizing Amazon Ads…",
    "run_meta_ads": "Optimizing Meta Ads…",
    "run_google_ads": "Optimizing Google Ads…",
    "create_fulfillment_rule": "Creating fulfillment rule…",
}

SYSTEM_PROMPT = """You are Orchestre, an intelligent e-commerce automation assistant.
You help business owners manage and optimize their Shopify and Amazon stores through natural conversation.

## Your capabilities
- **View data**: orders, products, inventory, price history, ad metrics, fulfillment logs
- **Run optimizations**: repricing, inventory restock forecasting, ad bid optimization, auto-fulfillment
- **Manage automation**: create fulfillment rules, trigger jobs, review automation history

## Connected stores
{stores_context}

## Critical safety rules
1. **Always dry_run first**: Before any optimization that modifies data (prices, bids, fulfillments), ALWAYS call with dry_run=true first and show the user what would change.
2. **Require explicit confirmation**: Only call with dry_run=false after the user has seen the preview and explicitly confirmed ("yes", "apply", "do it", "go ahead").
3. **Clarify which store**: If the user hasn't specified a store and they have more than one, ask first.
4. **Never invent data**: Only report what the tools return. If a tool returns an error, say so clearly.

## Response style
- Be concise and business-focused
- Lead with the most important insight
- Use bullet points for lists of 3+ items
- Bold key metrics: **$1,247 revenue**, **47 orders**, **3 SKUs low on stock**
- When showing optimization previews, clearly label them as "Preview — not applied yet"
- After a dry run, always end with: "Shall I apply these changes?"
"""


class CommerceAgent:

    def __init__(self) -> None:
        self._openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._redis: aioredis.Redis | None = None

    def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def _history_key(self, user_id: str, conversation_id: str) -> str:
        return f"agent_conv:{user_id}:{conversation_id}"

    async def _load_history(self, user_id: str, conversation_id: str) -> list[dict]:
        try:
            raw = await self._get_redis().get(self._history_key(user_id, conversation_id))
            return json.loads(raw) if raw else []
        except Exception:
            return []

    async def _save_history(
        self, user_id: str, conversation_id: str, history: list[dict]
    ) -> None:
        try:
            await self._get_redis().set(
                self._history_key(user_id, conversation_id),
                json.dumps(history, default=str),
                ex=HISTORY_TTL,
            )
        except Exception as e:
            logger.warning("Could not save conversation history: %s", e)

    async def _build_system_prompt(self, user_id: str) -> str:
        try:
            from agents.tools import tool_list_stores
            result = await tool_list_stores(user_id=user_id)
            stores = result.get("stores", [])
            if stores:
                lines = [
                    f"  - {s['identifier']} ({s['provider'].replace('provider', '')})"
                    for s in stores
                ]
                ctx = "The user has the following connected stores:\n" + "\n".join(lines)
            else:
                ctx = "The user has no connected stores yet. Guide them to connect one via the /providers/link_provider endpoint."
        except Exception:
            ctx = "Could not load store list."
        return SYSTEM_PROMPT.format(stores_context=ctx)

    async def run(
        self,
        user_id: str,
        message: str,
        conversation_id: str,
    ) -> AsyncIterator[dict]:
        """
        Async generator yielding SSE events:
          {"type": "status",  "content": "Checking inventory…"}
          {"type": "token",   "content": "You have 3 SKUs…"}
          {"type": "done",    "conversation_id": "..."}
          {"type": "error",   "content": "…"}
        """
        history = await self._load_history(user_id, conversation_id)
        system_prompt = await self._build_system_prompt(user_id)

        messages: list[dict] = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": message}]
        )

        full_response_text = ""

        try:
            for turn in range(MAX_TOOL_TURNS):
                # Non-streaming call to detect tool use
                response = await self._openai.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=0.3,
                )

                choice = response.choices[0]
                msg = choice.message

                if msg.tool_calls:
                    # Add assistant message with tool calls
                    messages.append(msg.model_dump(exclude_unset=False))

                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        status = TOOL_STATUS_LABELS.get(fn_name, f"Running {fn_name}…")
                        yield {"type": "status", "content": status}

                        tool_result = await execute_tool(
                            name=fn_name,
                            arguments=tc.function.arguments,
                            user_id=user_id,
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result,
                        })

                    # Loop — let model respond to tool results
                    continue

                # No tool calls — stream the final text response token by token
                # We re-call with stream=True using the full accumulated messages.
                # This gives true streaming for the final answer.
                messages.append({"role": "assistant", "content": msg.content or ""})
                # Remove the last assistant message and re-request with streaming
                messages.pop()

                stream = await self._openai.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="none",  # final answer only, no more tool calls
                    stream=True,
                    temperature=0.3,
                )

                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_response_text += delta
                        yield {"type": "token", "content": delta}

                break

            # Persist updated history (without system prompt)
            history.append({"role": "user", "content": message})
            if full_response_text:
                history.append({"role": "assistant", "content": full_response_text})

            # Trim to last 40 messages to keep context manageable
            if len(history) > 40:
                history = history[-40:]

            await self._save_history(user_id, conversation_id, history)

        except Exception as e:
            logger.exception("Agent error: %s", e)
            yield {"type": "error", "content": f"Something went wrong: {e}"}

        yield {"type": "done", "conversation_id": conversation_id}


# Singleton
agent = CommerceAgent()
