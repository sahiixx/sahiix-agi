"""Async Redis cache layer using redis.asyncio (Python 3.12+ compatible)."""
import asyncio
import json
import os
from typing import Any, Optional, List, Callable

try:
    from redis.asyncio import Redis as AsyncRedis
    from redis.asyncio import ConnectionPool
except Exception as _e:  # pragma: no cover
    AsyncRedis = None
    ConnectionPool = None


class AsyncRedisCache:
    """Lightweight async Redis wrapper with auto-reconnect and connection pooling.

    Replaces deprecated aioredis with redis.asyncio for Python 3.12+ compatibility.
    """

    def __init__(self, url: Optional[str] = None, max_connections: int = 10):
        self.url = url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.max_connections = max_connections
        self._pool: Optional[Any] = None
        self._lock = asyncio.Lock()

    async def connect(self):
        if AsyncRedis is None:
            raise RuntimeError("redis[async] is not installed")
        async with self._lock:
            if self._pool is None:
                self._pool = ConnectionPool.from_url(
                    self.url, max_connections=self.max_connections
                )
        return self

    async def disconnect(self):
        async with self._lock:
            if self._pool:
                await self._pool.disconnect()
                self._pool = None

    def _client(self):
        if self._pool is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return AsyncRedis(connection_pool=self._pool)

    async def get(self, key: str) -> Optional[Any]:
        try:
            value = await self._client().get(key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except Exception:
                return value.decode() if isinstance(value, bytes) else value
        except Exception:
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        try:
            payload = json.dumps(value) if not isinstance(value, (str, bytes)) else value
            client = self._client()
            if ttl:
                await client.setex(key, ttl, payload)
            else:
                await client.set(key, payload)
            return True
        except Exception:
            return False

    async def delete(self, key: str) -> bool:
        try:
            result = await self._client().delete(key)
            return result > 0
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        try:
            result = await self._client().exists(key)
            return result == 1
        except Exception:
            return False

    async def publish(self, channel: str, message: Any) -> bool:
        try:
            payload = json.dumps(message) if not isinstance(message, (str, bytes)) else message
            await self._client().publish(channel, payload)
            return True
        except Exception:
            return False

    async def subscribe(self, *channels: str) -> Any:
        """Return a pubsub object subscribed to the given channels."""
        pubsub = self._client().pubsub()
        await pubsub.subscribe(*channels)
        return pubsub

    @staticmethod
    async def pubsub_listen(pubsub: Any, handler: Callable[[str, Any], None]):
        """Listen on a pubsub object and invoke handler(channel, message) per message."""
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                channel = msg.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = msg.get("data", b"")
                if isinstance(data, bytes):
                    try:
                        data = json.loads(data.decode())
                    except Exception:
                        data = data.decode()
                try:
                    handler(channel, data)
                except Exception:
                    pass
