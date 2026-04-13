import redis.asyncio as redis_asyncio

from core.config.config import settings


class Redis:
    client: redis_asyncio.Redis | None = None

    async def connect(self):
        self.client = redis_asyncio.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )

    async def disconnect(self):
        if self.client is not None:
            await self.client.aclose()

    async def get_redis(self):
        yield self.client


redis_cache = Redis()
