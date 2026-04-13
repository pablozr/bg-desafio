from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config.config import settings
from core.logger.logger import logger
from core.postgresql.postgresql import postgresql
from core.rabbitmq.rabbitmq import rabbitmq
from core.redis.redis import redis_cache
from routes.auth.router import router as auth_router
from routes.users.router import router as users_router
from routes.products.router import router as products_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting service connections...")

    await postgresql.connect()
    await redis_cache.connect()
    await rabbitmq.connect()

    logger.info("All services connected successfully.")

    yield

    await postgresql.disconnect()
    await redis_cache.disconnect()
    await rabbitmq.disconnect()


app = FastAPI(
    lifespan=lifespan,
    title="FastAPI Template",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(products_router, prefix="/products", tags=["products"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.API_PORT)
