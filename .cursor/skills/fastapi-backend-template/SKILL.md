---
name: fastapi-backend-template
description: >-
  Architecture, conventions, and rules for a generic FastAPI backend with
  authentication and user management. Covers project structure, database access,
  caching, messaging, security, and coding patterns. Follow these instructions
  exactly whenever generating, modifying, or reviewing code in this project.
  Use when working on FastAPI, asyncpg, Redis, RabbitMQ, JWT, bcrypt, Pydantic,
  auth routes, or user services in this template.
---

# FastAPI Backend — Generic Skill

This skill defines ALL architecture decisions, coding conventions, and business rules for a FastAPI backend. The scope covers authentication (login, Google OAuth, password reset) and user management (create, read, update). Use this as the baseline for any new module you add.

## 1. Stack
| Layer | Technology |
| :--- | :--- |
| **Language** | Python 3.12+ |
| **Framework** | FastAPI |
| **Database** | PostgreSQL via `asyncpg` (async driver) |
| **Cache** | Redis via `redis-py` (async, `redis.asyncio`) |
| **Messaging** | RabbitMQ via `aio-pika` |
| **Validation** | Pydantic V2 + `pydantic-settings` |
| **Auth tokens** | PyJWT (`jwt`) |
| **Password hashing** | `bcrypt` |
| **Google Auth** | `google.oauth2.id_token` + `google.auth.transport.requests` |

> [!CAUTION]
> Never use SQLAlchemy or any ORM. All database access MUST use raw parameterized SQL through `asyncpg`.

## 2. Project Structure
```text
/ (project root)
├── core/
│   ├── config/config.py          # Settings (env) + app constants below `settings` (cookies, TTLs, queue, RBAC)
│   ├── postgresql/postgresql.py  # asyncpg pool singleton
│   ├── redis/redis.py            # Redis async singleton
│   ├── rabbitmq/rabbitmq.py      # aio-pika singleton
│   ├── security/hashing.py       # bcrypt hash_password / verify_password only
│   ├── security/jwt_payloads.py  # Pure dict builders for JWT claims (auth / reset); no I/O
│   ├── security/rate_limit.py    # Redis-based rate_limiter() dependency for public routes
│   ├── security/security.py      # encode/decode JWT, Google OAuth, verify_token / validate_token, Depends
│   └── logger/logger.py          # Structured logging (stdlib logging)
├── routes/
│   ├── auth/router.py            # login, google-login, logout, forget-password, validate-code, update-password
│   └── users/router.py           # GET /me, PUT /me, POST /users
├── services/
│   ├── auth/auth_service.py      # Auth business logic (async); uses core.security.jwt_payloads for claims
│   ├── user/user_service.py      # User business logic + DB queries
│   ├── cache/cache_service.py    # Redis get/set/delete helpers
│   └── messaging/messaging_service.py  # RabbitMQ publish helper
├── schemas/
│   ├── auth.py                   # LoginRequestModel, ValidateCodeRequest, UpdatePasswordRequest, etc.
│   └── user.py                   # UserCreateRequest, UserUpdateRequest, UserGetResponse (TypedDict)
├── functions/
│   └── utils/utils.py            # default_response, update_default_dict, generate_temp_code
├── workers/
│   └── smtp/email_worker.py      # RabbitMQ email consumer (standalone process)
├── templates/
│   └── email.py                  # HTML email string templates with placeholders
├── main.py                       # FastAPI app + lifespan (connect/disconnect all services)
├── requirements.txt
└── .env.example
```

> [!IMPORTANT]
> Every module folder must have its own `__init__.py`. Use full import paths: `from core.security import security`, `from services.auth import auth_service`.

> [!NOTE]
> **Constants** (`COOKIE_*`, queue name, cookie `max_age` values, `ROLE_RANK_BY_NAME`, `RATE_LIMIT_*` for Redis limiters) live in `core/config/config.py` immediately after `settings = Settings()` (not from env). **Password hashing** lives in `core/security/hashing.py`. **JWT claim dicts** (`type`, `userId`, `canUpdate`, etc.) live in `core/security/jwt_payloads.py` next to encoding rules — not under `services/`, because they are not business orchestration. **Service modules** contain orchestration and DB/cache/messaging only — no inline module-level constants scattered mid-file, and no lazy imports except where unavoidable.

## 3. Core Infrastructure Singletons
Each infrastructure service is a class with a singleton instance that exposes `connect()`, `disconnect()`, and a FastAPI-compatible `Depends` generator.

### PostgreSQL (`core/postgresql/postgresql.py`)
```python
import asyncpg
from typing import Optional
from core.config.config import settings

class PostgreSQL:
    pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            dsn=f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}",
            min_size=1,
            max_size=3,
        )

    async def disconnect(self):
        await self.pool.close()

    async def get_db(self):
        async with self.pool.acquire() as conn:
            yield conn

postgresql = PostgreSQL()
```
*Usage in routes:* `conn: asyncpg.Connection = Depends(postgresql.get_db)`

### Redis (`core/redis/redis.py`)
```python
import redis.asyncio
from core.config.config import settings

class Redis:
    redis: redis.asyncio.Redis = None

    async def connect(self):
        self.redis = redis.asyncio.Redis(
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
```
*Usage in routes:* `redis_client = Depends(redis_cache.get_redis)`

### RabbitMQ (`core/rabbitmq/rabbitmq.py`)
```python
import aio_pika
from aio_pika import connect_robust
from aio_pika.abc import AbstractChannel
from core.config.config import settings

class RabbitMQ:
    connection = None
    channel: AbstractChannel | None = None

    async def connect(self):
        self.connection = await connect_robust(
            f"amqp://{settings.RABBITMQ_USER}:{settings.RABBITMQ_PASSWORD}@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/"
        )
        self.channel = await self.connection.channel()

    async def disconnect(self):
        await self.connection.close()

    async def get_channel(self):
        yield self.channel

rabbitmq = RabbitMQ()
```
*Usage in routes:* `clientmq = Depends(rabbitmq.get_channel)`

### Lifespan (`main.py`)
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.logger.logger import logger
from core.postgresql.postgresql import postgresql
from core.redis.redis import redis_cache
from core.rabbitmq.rabbitmq import rabbitmq
from routes.auth.router import router as auth_router
from routes.users.router import router as users_router

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router,  prefix="/auth",  tags=["auth"])
app.include_router(users_router, prefix="/users", tags=["users"])
```
> [!CAUTION]
> Never use `print()` anywhere. Always use `logger.info()`, `logger.error()`, or `logger.exception()`.

### Workers (standalone processes)
Workers reuse the same singleton classes from `core/` but manage their own `connect()` / `disconnect()`:
```python
# workers/smtp/email_worker.py
from core.rabbitmq.rabbitmq import rabbitmq

async def start_email_worker():
    await rabbitmq.connect()
    await rabbitmq.channel.set_qos(prefetch_count=1)
    queue = await rabbitmq.channel.declare_queue("email-queue", durable=True)
    await queue.consume(process_email)
    try:
        await asyncio.Future()
    finally:
        await rabbitmq.disconnect()
```
> [!CAUTION]
> Never create raw connections in workers (e.g. `await connect_robust(...)` directly). Always use the singleton classes from `core/`.

## 4. Configuration (`core/config/config.py`)
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    API_PORT: int = 8000

    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    RABBITMQ_HOST: str
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str
    RABBITMQ_PASSWORD: str

    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    SMTP_HOST: str
    SMTP_PORT: int = 587
    SMTP_USER: str
    SMTP_PASSWORD: str
    EMAIL_FROM: str

    GOOGLE_CLIENT_ID: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
```
*Access anywhere:* `from core.config.config import settings`

> [!CAUTION]
> Never hardcode config values (hosts, secrets, emails). Always read from `settings.FIELD`.

## 5. Logger (`core/logger/logger.py`)
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(funcName)s() | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
```
*Import:* `from core.logger.logger import logger`

| Situation | Method |
| :--- | :--- |
| Expected / handled error | `logger.error(e)` |
| Unexpected error (need traceback) | `logger.exception(e)` |
| Lifecycle / info events | `logger.info("message")` |

## 6. Request–Response Flow

### Service return contract
Every service function must return this exact shape:
```python
# Success
{"status": True,  "message": "Human-readable message", "data": { ... }}

# Failure — "data": {} is MANDATORY, never omit it
{"status": False, "message": "Error description", "data": {}}
```

### Pattern A — default_response wrapper (all non-auth routes)
```python
# functions/utils/utils.py
from fastapi.responses import JSONResponse

async def default_response(callable_function, params=[], is_creation=False):
    result = await callable_function(*params)
    if not result["status"]:
        return JSONResponse(status_code=400, content={"detail": result["message"]})
    status_code = 201 if is_creation else 200
    return JSONResponse(status_code=status_code, content={"message": result["message"], "data": result["data"]})
```
*Route example:*
```python
@router.get("/me")
async def get_me(user=Depends(security.validate_token_wrapper), conn=Depends(postgresql.get_db)):
    return await default_response(user_service.get_by_id, [conn, user["userId"]])
```

### Pattern B — Inline response (auth routes that set cookies)
Auth routes handle the response directly to set HttpOnly cookies:
```python
@router.post("/login")
async def login(data: LoginRequestModel, conn=Depends(postgresql.get_db)):
    response = await auth_service.login(conn, data)
    if not response["status"]:
        return JSONResponse(status_code=400, content={"detail": response["message"]})

    token = response["data"].pop("access_token")
    resp = JSONResponse(status_code=200, content={"message": response["message"], "data": response["data"]})
    resp.set_cookie(key="auth", value=token, httponly=True, secure=True, samesite="lax", path="/", max_age=259200)
    return resp
```

## 7. Database Conventions
* **Driver:** `asyncpg` exclusively — connection type hint: `conn: asyncpg.Connection`
* **Placeholders:** positional `$1`, `$2`, `$3` — **never** f-string interpolation of values
* `fetchrow()` → single row (`SELECT` / `INSERT RETURNING`)
* `fetch()` → multiple rows
* `execute()` → `INSERT` / `UPDATE` / `DELETE` without return
* `async with conn.transaction():` → for atomic multi-statement operations
* **Every** `UPDATE` query must include `updated_at = NOW()`

### Dynamic UPDATE (safe whitelist pattern)
```python
allowed_columns = {"fullname", "email"}  # hardcoded — never sourced from request
filtered = {k: v for k, v in data.model_dump(exclude_none=True).items() if k in allowed_columns}

if not filtered:
    return {"status": False, "message": "No fields to update", "data": {}}

columns = list(filtered.keys())
values  = list(filtered.values())
set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(columns, 1))
values.append(user_id)

query = f"UPDATE users SET {set_clause}, updated_at = NOW() WHERE id = ${len(values)} RETURNING *"
row = await conn.fetchrow(query, *values)
```
> [!CAUTION]
> Column names MUST come from the hardcoded `allowed_columns` set, never from request data. Only `$N` placeholders carry user-supplied values.

### IDOR protection
> [!IMPORTANT]
> Every `SELECT`, `UPDATE`, and `DELETE` on user-owned data MUST include `AND user_id = $X` to enforce multi-tenant isolation.

## 8. Database Schema
### `users` table
```sql
CREATE TABLE users (
    id         SERIAL PRIMARY KEY,
    fullname   VARCHAR(255) NOT NULL,
    email      VARCHAR(255) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,        -- always bcrypt hash
    role       VARCHAR(50)  NOT NULL DEFAULT 'BASIC', -- 'BASIC' | 'ADMIN'
    created_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP    NOT NULL DEFAULT NOW()
);
```
> [!NOTE]
> The column is `password`, not `password_hash`. The stored value is always a bcrypt hash.

## 9. Authentication & Security (`core/security/security.py`)

### Password hashing (`core/security/hashing.py`)
Keep bcrypt isolated here so `user_service` and `auth_service` import hashing without pulling in JWT or `user_service` (avoids circular imports with `security.py`).

```python
import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
```
*Import:* `from core.security.hashing import hash_password, verify_password`

### JWT
```python
from datetime import datetime, timedelta, timezone
import jwt
from core.config.config import settings

def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload.update({"exp": expire})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
```
**JWT payload fields:** `userId`, `email`, `fullname`, `role`, `type` ("auth" or "reset"); for reset flow also `canUpdate` (`False` until the code is validated, then `True`).

### Cookie settings
| Property | Login (`auth`) | Password reset (`auth_reset`) |
| :--- | :--- | :--- |
| **httponly** | `True` | `True` |
| **secure** | `True` | `True` |
| **samesite** | `"lax"` | `"lax"` |
| **path** | `"/"` | `"/"` |
| **max_age** | `259200` (3 days) | `900` (15 min) |

### Token validation (`verify_token` + `validate_token` + Depends wrappers)

Core rules:

* **`verify_token`** — strips optional `Bearer ` prefix, decodes via `decode_access_token`, checks `type`, reloads the user with **`user_service.get_one_user`**, returns the same shape as `data["user"]` (`userId`, `fullname`, `email`, `role`, `createdAt`).  
  * Return **`None`** → token expired (`ExpiredSignatureError`).  
  * Return **`False`** → invalid token (`InvalidTokenError`, wrong type/stage, user missing, etc.).  
* **`validate_token`** — reads cookie `auth` or `auth_reset` (`reset_cookie=True`), calls `verify_token`, maps `None` → HTTP 401 `"Token has expired"`, falsy → HTTP 401 `"Invalid token"`, sets **`request.state.token`**, returns the user dict.  
  * Use **`except HTTPException: raise`** before a generic `except` so FastAPI errors are not swallowed.

```python
async def verify_token(
    token: str,
    conn: asyncpg.Connection,
    check_can_update: bool = False,
    expected_type: str = "auth",
) -> dict | bool | None:
    try:
        if token.startswith("Bearer "):
            token = token[7:]
        payload = decode_access_token(token)
        if payload.get("type") != expected_type:
            raise jwt.InvalidTokenError("Token type mismatch")
        # Reset step 2: must still be canUpdate=False (enforced when expected_type=="reset" and not check_can_update)
        if expected_type == "reset" and not check_can_update:
            if payload.get("canUpdate") is not False:
                raise jwt.InvalidTokenError("Invalid reset token stage")
        if not payload.get("userId"):
            raise jwt.InvalidTokenError("Invalid token payload")
        response = await user_service.get_one_user(conn, payload["userId"])
        if response["status"] is None or not response["status"]:
            raise jwt.InvalidSignatureError("User not found")
        if check_can_update:
            if payload.get("canUpdate"):
                return dict(response["data"]["user"])
            raise jwt.InvalidTokenError("User does not have update permissions")
        return dict(response["data"]["user"])
    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        return None
    except jwt.InvalidTokenError:
        logger.error("Invalid token")
        return False


async def validate_token(
    request: Request,
    conn: asyncpg.Connection,
    check_can_update: bool = False,
    reset_cookie: bool = False,
    expected_type: str = "auth",
) -> dict:
    try:
        cookie_key = COOKIE_AUTH if not reset_cookie else COOKIE_AUTH_RESET  # from core.config.config
        token = request.cookies.get(cookie_key)
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user = await verify_token(
            token, conn=conn, check_can_update=check_can_update, expected_type=expected_type
        )
        if user is None:
            raise HTTPException(status_code=401, detail="Token has expired")
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        request.state.token = token
        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=401, detail="Invalid token")
```

**FastAPI Depends wrappers**

```python
# Login session — cookie "auth", type "auth"
async def validate_token_wrapper(
    request: Request, conn=Depends(postgresql.get_db)
) -> dict:
    return await validate_token(request, conn)


# Reset step 2 — cookie "auth_reset", type "reset", canUpdate must be False
async def validate_token_to_validate_code(
    request: Request, conn=Depends(postgresql.get_db)
) -> dict:
    return await validate_token(
        request, conn, check_can_update=False, reset_cookie=True, expected_type="reset"
    )


# Reset step 3 — cookie "auth_reset", type "reset", canUpdate must be True
async def validate_token_to_update_password(
    request: Request, conn=Depends(postgresql.get_db)
) -> dict:
    return await validate_token(
        request, conn, check_can_update=True, reset_cookie=True, expected_type="reset"
    )
```

*Usage in routes:*
```python
# Protect a route (no user data needed in handler)
@router.get("/me", dependencies=[Depends(security.validate_token_wrapper)])

# Protect and inject user dict
async def my_route(user=Depends(security.validate_token_wrapper)):
    user["userId"]  # internal queries
```

### Role-based access control (RBAC)
```python
def require_minimum_rank(minimum_rank: int):
    async def dependency(user=Depends(security.validate_token_wrapper)):
        rank = ROLE_RANK_BY_NAME.get(user.get("role", "").upper(), 0)  # from core.config.config
        if rank < minimum_rank:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency

def require_admin_rank():
    return require_minimum_rank(2)
```
*Usage:* `@router.get("/admin", dependencies=[Depends(security.require_admin_rank())])`

### Google OAuth2
```python
from google.oauth2 import id_token
from google.auth.transport import requests

def verify_google_token(token: str) -> dict | None:
    try:
        return id_token.verify_oauth2_token(token, requests.Request(), settings.GOOGLE_CLIENT_ID)
    except Exception:
        return None
```
*On Google login:* if user doesn't exist, create them with `secrets.token_urlsafe(32)` as password (bcrypt-hashed).

## 10. Password Reset Flow
Three sequential endpoints using Redis for temporary codes and two-phase JWT tokens:

1.  **Step 1 — POST `/auth/forget-password`**
    * Find user by email in DB
    * Generate a 6-digit code via `generate_temp_code()`
    * Store in Redis: key = `"{user_id}:{email}"`, value = `{"code": "123456"}`, TTL = `600 s`
    * Publish email with the code to `email-queue` via RabbitMQ
    * Return JWT with `type: "reset"`, `canUpdate: False` → set in cookie `auth_reset` (max_age 900 s)
2.  **Step 2 — POST `/auth/validate-code`**
    * Guard: `validate_token_to_validate_code` (requires `type=reset`, `canUpdate=False`)
    * Compare submitted code with `redis_code.get("code")`
    * If valid: delete Redis key → return new JWT with `canUpdate: True` in cookie `auth_reset`
3.  **Step 3 — POST `/auth/update-password`**
    * Guard: `validate_token_to_update_password` (requires `type=reset`, `canUpdate=True`)
    * Hash new password → update DB
    * Delete `auth_reset` cookie

**Redis key pattern**
```python
cache_key = f'{user["userId"]}:{user["email"]}'  # e.g. "42:user@example.com"
```

> [!CAUTION]
> When building f-strings with dict access, never nest quotes of the same type.
> ✅ `f'{row["id"]}:{data.email}'` — alternating quotes
> ❌ `f"{row["id"]}:{data.email}"` — will raise SyntaxError

## 11. Cache Service (`services/cache/cache_service.py`)
```python
import json

async def get_by_key(key: str, redis_client) -> dict | bool:
    raw = await redis_client.get(key)
    return json.loads(raw) if raw else False

async def set_by_key(key: str, ttl_seconds: int, value: dict, redis_client) -> None:
    await redis_client.setex(key, ttl_seconds, json.dumps(value, default=str))

async def delete_by_key(key: str, redis_client) -> None:
    await redis_client.delete(key)
```
> [!NOTE]
> `get_by_key` returns a parsed dict, not a raw string. Access fields directly: `redis_data.get("code")`.

## 12. Messaging Service (`services/messaging/messaging_service.py`)
```python
import json
import aio_pika
from aio_pika import Message

async def publish(queue_name: str, payload: dict, channel: aio_pika.abc.AbstractChannel) -> None:
    message = Message(body=json.dumps(payload).encode(), delivery_mode=2)  # persistent
    await channel.default_exchange.publish(message, routing_key=queue_name)
```
*Standard email payload:*
```json
{
    "to":                   "recipient@example.com",
    "from":                 "settings.EMAIL_FROM",
    "subject":              "Email subject",
    "html":                 "<html>...</html>",
    "message":              "",
    "base64Attachment":     "",
    "base64AttachmentName": ""
}
```
(Always use `settings.EMAIL_FROM` in code, never hardcode.)

## 13. Pydantic Schemas (`schemas/`)
### Conventions
* Frontend sends `camelCase` → backend stores `snake_case`
* Use `Field(alias="camelName")` + `model_config = {"populate_by_name": True}` for translation
* Use `TypedDict` for typing service return shapes (not for request validation)
* All schemas live in `schemas/` — never define them inline in routers
* Use `field_validator` for normalization (e.g. `.upper()`, `.strip()`) — never transform in the service layer
* Use native Pydantic types (`date`, `Decimal`, etc.) — never manual string parsing
* **DB → API mappers** for response shapes (e.g. `user_from_row` mapping `id` → `userId`) live in the same `schemas/` module as the `TypedDict` / models they serve — not in `services/`

### Create vs Update pattern
```python
# schemas/user.py
import asyncpg
from pydantic import BaseModel, Field, field_validator
from typing import TypedDict

# CREATE — all fields required, access directly (data.fullname)
class UserCreateRequest(BaseModel):
    fullname: str = Field(min_length=1)
    email:    str = Field(min_length=5)
    password: str = Field(min_length=6)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

# UPDATE — all optional, use model_dump(exclude_none=True)
class UserUpdateRequest(BaseModel):
    fullname: str | None = Field(default=None, min_length=1)
    email:    str | None = Field(default=None, min_length=5)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else v

# RESPONSE SHAPE — TypedDict (for type hints only)
class UserData(TypedDict):
    userId:     int
    fullname:   str
    email:      str
    role:       str
    createdAt:  str

class UserGetResponse(TypedDict):
    status:  bool
    message: str
    data:    UserData

# Mapper: asyncpg row → API user (single place for userId / createdAt)
def user_from_row(row: asyncpg.Record) -> UserData:
    return {
        "userId": row["id"],
        "fullname": row["fullname"],
        "email": row["email"],
        "role": row["role"],
        "createdAt": str(row["created_at"]),
    }
```

### Auth schemas
```python
# schemas/auth.py
class LoginRequestModel(BaseModel):
    email:    str
    password: str

class LoginGoogleRequestModel(BaseModel):
    token: str  # Google ID token from frontend

class ForgetPasswordRequestModel(BaseModel):
    email: str

class ValidateCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)

class UpdatePasswordRequest(BaseModel):
    password: str = Field(min_length=6)
```

## 14. Response Field Naming
Services that return user data must expose `userId` (not raw DB `id`). Use **`schemas.user.user_from_row(row)`** — do not duplicate the dict literal in services.

```python
from schemas.user import user_from_row

return {
    "status": True,
    "message": "User retrieved successfully",
    "data": {"user": user_from_row(row)},
}
```
> [!IMPORTANT]
> `verify_token` / `validate_token` return the user dict from `user_service.get_one_user` (`data["user"]`), including `userId`. Always use `userId` in JWT payloads and API responses, never a duplicate `id` key.

## 15. Utility Functions (`functions/utils/utils.py`)
```python
import secrets
import string
from fastapi.responses import JSONResponse

# --- HTTP response wrapper ---
async def default_response(callable_function, params=[], is_creation=False):
    result = await callable_function(*params)
    if not result["status"]:
        return JSONResponse(status_code=400, content={"detail": result["message"]})
    status_code = 201 if is_creation else 200
    return JSONResponse(status_code=status_code, content={"message": result["message"], "data": result["data"]})

# --- Serialization helper ---
def serialize_row(row: dict, date_fields: list[str] = [], decimal_fields: list[str] = []) -> dict:
    """Convert asyncpg row types to JSON-serializable values."""
    result = dict(row)
    for f in date_fields:
        if result.get(f) is not None:
            result[f] = str(result[f])
    for f in decimal_fields:
        if result.get(f) is not None:
            result[f] = float(result[f])
    return result

# --- Password reset code ---
def generate_temp_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))
```

## 16. Email Templates (`templates/email.py`)
Store HTML templates as Python string constants with named placeholders. Replace before sending:

```python
# templates/email.py

WELCOME_EMAIL_TEMPLATE = """
<html>
  <body>
    <h1>Welcome, NAME_HERE!</h1>
    <p>Your account has been created. Your temporary password is: <strong>PASSWORD_HERE</strong></p>
  </body>
</html>
"""

RESET_PASSWORD_EMAIL_TEMPLATE = """
<html>
  <body>
    <h1>Password Reset</h1>
    <p>Your verification code is: <strong>CODE_HERE</strong></p>
    <p>This code expires in 10 minutes.</p>
  </body>
</html>
"""
```
*Usage:*
`html = RESET_PASSWORD_EMAIL_TEMPLATE.replace("CODE_HERE", code)`

## 17. Error Handling Rules
* Catch specific `asyncpg` exceptions first, then fall back to generic `Exception`
* Return friendly messages — never expose raw tracebacks in API responses
* Log with `logger.error(e)` for expected errors, `logger.exception(e)` for unexpected ones

```python
from asyncpg.exceptions import UniqueViolationError

try:
    row = await conn.fetchrow("INSERT INTO users ...")
except UniqueViolationError:
    return {"status": False, "message": "Email already registered", "data": {}}
except Exception as e:
    logger.error(e)
    return {"status": False, "message": "Internal server error", "data": {}}
```

## 18. Adding a New Module (Checklist)
When extending the template with a new domain (e.g. products, orders), follow this checklist:

* [ ] Create `routes/<domain>/router.py` and register in `main.py`
* [ ] Create `services/<domain>/<domain>_service.py`
* [ ] Create schemas in `schemas/<domain>.py` (not inline)
* [ ] Add domain table migration with `user_id` FK and `updated_at` column
* [ ] Use `default_response` wrapper for all non-auth routes
* [ ] All DB queries use `$N` placeholders — no ORM, no raw string interpolation
* [ ] All `UPDATE` queries include `updated_at = NOW()`
* [ ] All owner-scoped queries include `AND user_id = $X`
* [ ] Dynamic updates use `allowed_columns` whitelist + `model_dump(exclude_none=True)`
* [ ] Catch `UniqueViolationError` and other specific `asyncpg` exceptions before generic `Exception`
* [ ] Pydantic field transformations in `field_validator`, never in the service layer
* [ ] `camelCase` aliases via `Field(alias=...)` when frontend sends camelCase keys
* [ ] Service returns always include `"data": {}` on failure
* [ ] User API dict uses `schemas.user.user_from_row` (or equivalent mapper in `schemas/`) — no duplicate `id` key
* [ ] Log via `logger`, never via `print()`
* [ ] Config values always via `settings.FIELD`, never hardcoded
* [ ] Sensitive unauthenticated routes use `rate_limiter` (see §21)

## 19. requirements.txt
```text
fastapi==0.115.0
uvicorn[standard]==0.30.6
asyncpg==0.29.0
redis==5.0.8
aio-pika==9.4.3
pydantic==2.9.2
pydantic-settings==2.5.2
PyJWT==2.9.0
bcrypt==4.2.0
google-auth==2.35.0
python-multipart==0.0.12
```

## 20. .env.example
```text
# Application
ENVIRONMENT=development
API_PORT=8000

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=your_db

# RabbitMQ
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# JWT
SECRET_KEY=change_this_to_a_long_random_string
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASSWORD=your_app_password
EMAIL_FROM=your@email.com

# Google OAuth
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
```

## 21. Rate Limiting (`core/security/rate_limit.py`)

To protect sensitive public routes (for example `/auth/login`, `/auth/google-login`, `/auth/forget-password`, `/auth/validate-code`) from brute force and spam, use a **Redis-based dependency**. Do not rely on bulky third-party rate-limit stacks unless you have a strong reason.

```python
from fastapi import Depends, HTTPException, Request

from core.redis.redis import redis_cache


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def rate_limiter(max_requests: int, window_seconds: int):
    async def dependency(
        request: Request, redis_client=Depends(redis_cache.get_redis)
    ):
        ip = _client_ip(request)
        key = f"rate_limit:{request.url.path}:{ip}"

        current = await redis_client.incr(key)
        if current == 1:
            await redis_client.expire(key, window_seconds)

        if current > max_requests:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
            )
        return True

    return dependency
```

**Usage:** expose ready-made dependency lists at the **bottom of `rate_limit.py`** (after `rate_limiter`), built from **`RATE_LIMIT_*` in `core/config/config.py`**, e.g. `LOGIN_RATE_LIMIT_DEPS`, `FORGET_PASSWORD_RATE_LIMIT_DEPS`. The auth router imports those — do not redefine `Depends(rate_limiter(...))` blocks in `router.py`.

```python
# core/security/rate_limit.py (excerpt)
LOGIN_RATE_LIMIT_DEPS = [
    Depends(rate_limiter(RATE_LIMIT_LOGIN_MAX_REQUESTS, RATE_LIMIT_LOGIN_WINDOW_SECONDS))
]

# routes/auth/router.py
@router.post("/login", dependencies=LOGIN_RATE_LIMIT_DEPS)
async def login(...):
    ...
```

> [!IMPORTANT]
> Always derive the client IP from **`X-Forwarded-For` first** (use the **first** address in the list — original client), then fall back to `request.client.host`. The backend usually sits behind a reverse proxy (Nginx, Traefik) or load balancer.
