"""
Token cache backed by Redis.

Solves two problems:
1. OAuth access tokens (Amazon LWA, Google, Meta) expire every hour.
   Without caching, every single API call fetches a fresh token — wasteful
   and risks hitting LWA rate limits (10 req/sec per app).

2. Credentials (refresh tokens, shop tokens) should only be read from Firebase
   once per worker startup, then cached for the lifetime of the token.

Cache key pattern:
  token:{provider}:{account_key}    → access token string, TTL = token expiry - 60s buffer

Refresh tokens are never cached here — they live in Firebase and are loaded
on first use, then the resulting access token is cached.
"""
import logging
from typing import Optional

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

# Buffer before actual expiry to trigger refresh early (seconds)
EXPIRY_BUFFER_SECS = 60


class TokenCache:
    """
    Async Redis token cache.
    Thread-safe: uses a single Redis connection pool shared across the process.
    """

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=10,
            )
        return self._client

    def _key(self, provider: str, account_key: str) -> str:
        return f"token:{provider}:{account_key}"

    async def get(self, provider: str, account_key: str) -> Optional[str]:
        """Return cached access token if still valid, else None."""
        try:
            return await self._get_client().get(self._key(provider, account_key))
        except Exception as e:
            logger.debug("Token cache get failed (non-fatal): %s", e)
            return None

    async def set(
        self,
        provider: str,
        account_key: str,
        access_token: str,
        ttl_seconds: int = 3600,
    ) -> None:
        """Cache an access token with TTL. Stores TTL - buffer to refresh proactively."""
        effective_ttl = max(30, ttl_seconds - EXPIRY_BUFFER_SECS)
        try:
            await self._get_client().set(
                self._key(provider, account_key),
                access_token,
                ex=effective_ttl,
            )
        except Exception as e:
            logger.debug("Token cache set failed (non-fatal): %s", e)

    async def delete(self, provider: str, account_key: str) -> None:
        """Invalidate a cached token (e.g. after 401 response)."""
        try:
            await self._get_client().delete(self._key(provider, account_key))
        except Exception as e:
            logger.debug("Token cache delete failed (non-fatal): %s", e)

    async def get_or_refresh(
        self,
        provider: str,
        account_key: str,
        refresh_fn,
        ttl_seconds: int = 3600,
    ) -> str:
        """
        Return cached access token, or call refresh_fn() to get a new one
        and cache it. refresh_fn must be an async callable that returns
        (access_token: str, ttl_seconds: int).

        Usage:
            token = await token_cache.get_or_refresh(
                provider="amazon_lwa",
                account_key=seller_id,
                refresh_fn=lambda: lwa_refresh(refresh_token),
            )
        """
        cached = await self.get(provider, account_key)
        if cached:
            return cached

        new_token, ttl = await refresh_fn()
        await self.set(provider, account_key, new_token, ttl_seconds=ttl)
        return new_token

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton — shared across all providers in the same process
token_cache = TokenCache()
