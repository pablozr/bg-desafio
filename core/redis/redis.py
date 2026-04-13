import redis.asyncio as redis

from core.config.config import settings


class Redis:
    redis: redis.Redis = None

    async def connect(self):
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )

    async def disconnect(self):
        await self.redis.aclose()

    async def get_redis(self):
        yield self.redis


redis_cache = Redis()
