"""
AutoBot - dispatches recurring provider automation jobs to the ARQ queue.
State is persisted in Redis (via ARQ), not in-memory. This means the server
can restart and jobs will continue running on the worker process.
"""
import logging
from typing import Optional

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from core.config import settings
from core.utils.log import BackLog
from core.queue.worker import get_redis_settings

logger = logging.getLogger(__name__)

# Key pattern: autobot_status:{uid}:{provider}:{identifier}
_STATUS_PREFIX = "autobot_status"
# Default polling interval (seconds)
DEFAULT_INTERVAL = 30


class AutoBot:
    """
    Manages recurring provider automation jobs via ARQ.
    Jobs are enqueued into Redis; a separate worker process executes them.
    """

    def __init__(self) -> None:
        self._redis_settings = get_redis_settings()

    async def _get_pool(self) -> ArqRedis:
        return await create_pool(self._redis_settings)

    def _status_key(self, uid: str, provider: str, identifier: str) -> str:
        return f"{_STATUS_PREFIX}:{uid}:{provider}:{identifier}"

    async def start_auto_bot(
        self,
        user: Optional[dict],
        provider_name: str,
        identifier_name: str,
        interval: int = DEFAULT_INTERVAL,
    ) -> None:
        if user is None:
            return

        uid = user["uid"]
        key = self._status_key(uid, provider_name, identifier_name)

        pool = await self._get_pool()
        try:
            # Mark as active in Redis (TTL slightly longer than the interval)
            await pool.set(key, "active", ex=interval * 10)

            # Enqueue the first run immediately; subsequent runs are re-enqueued
            # from within the task itself using _defer pattern
            await pool.enqueue_job(
                "run_provider_autobot",
                user_id=uid,
                provider_name=provider_name,
                identifier_name=identifier_name,
                _job_id=f"autobot:{uid}:{provider_name}:{identifier_name}",
                _defer_by=0,
            )
            BackLog.info(
                instance=self,
                message=f"Enqueued autobot: {provider_name}/{identifier_name}",
            )
        finally:
            await pool.aclose()

    async def stop_auto_bot(
        self,
        user: Optional[dict],
        provider_name: str,
        identifier_name: str,
    ) -> None:
        if user is None:
            return

        uid = user["uid"]
        key = self._status_key(uid, provider_name, identifier_name)

        pool = await self._get_pool()
        try:
            await pool.delete(key)
            # ARQ doesn't support cancelling by job_id directly in all versions,
            # but the status key absence means the task won't re-enqueue itself.
            BackLog.info(
                instance=self,
                message=f"Stopped autobot: {provider_name}/{identifier_name}",
            )
        finally:
            await pool.aclose()

    async def status_auto_bot(
        self,
        user: Optional[dict],
        provider_name: str,
        identifier_name: str,
    ) -> bool:
        if user is None:
            return False

        uid = user["uid"]
        key = self._status_key(uid, provider_name, identifier_name)

        pool = await self._get_pool()
        try:
            value = await pool.get(key)
            return value is not None
        finally:
            await pool.aclose()

    async def status_my_auto_bot(self, user: Optional[dict]) -> dict:
        if user is None:
            return {}

        uid = user["uid"]
        pattern = f"{_STATUS_PREFIX}:{uid}:*"

        pool = await self._get_pool()
        try:
            keys = await pool.keys(pattern)
            result: dict = {}
            for key in keys:
                parts = key.decode().split(":")
                if len(parts) >= 4:
                    provider = parts[2]
                    identifier = ":".join(parts[3:])
                    if provider not in result:
                        result[provider] = {}
                    result[provider][identifier] = True
            return result
        finally:
            await pool.aclose()


autobot = AutoBot()
