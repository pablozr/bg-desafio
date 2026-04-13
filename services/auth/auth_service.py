import secrets
from datetime import timedelta

import aio_pika
import asyncpg
import redis

from core.config.config import (
    ACCESS_TOKEN_TTL_SECONDS,
    EMAIL_QUEUE,
    REFRESH_TOKEN_TTL_SECONDS,
    RESET_CODE_REDIS_TTL,
    RESET_COOKIE_MAX_AGE,
    settings,
)
from core.logger.logger import logger
from core.security.hashing import hash_password, verify_password
from core.security.jwt_payloads import reset_jwt_payload
from core.security.security import create_token, decode_access_token
from functions.utils.utils import generate_temp_code
from schemas.auth import (
    ForgetPasswordRequestModel,
    LoginRequestModel,
    UpdatePasswordRequest,
    ValidateCodeRequest,
)
from schemas.user import user_from_row
from services.cache import cache_service
from services.messaging import messaging_service
from templates.email import RESET_PASSWORD_EMAIL_TEMPLATE


async def login(
    conn: asyncpg.Connection, redis_client: redis.Redis, data: LoginRequestModel
) -> dict:
    try:
        row = await conn.fetchrow(
            """
            SELECT id, fullname, email, role, password, created_at
            FROM users WHERE email = $1
            """,
            data.email,
        )

        if not row or not verify_password(data.password, row["password"]):
            return {"status": False, "message": "Invalid email or password", "data": {}}

        session_id = secrets.token_hex(16)
        refresh_jti = secrets.token_hex(16)

        token = create_token(
            {
                "userId": row["id"],
                "email": row["email"],
                "fullname": row["fullname"],
                "role": row["role"],
                "sessionId": session_id,
                "type": "auth",
            },
            expires_delta=timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        )

        refresh_token = create_token(
            {
                "userId": row["id"],
                "email": row["email"],
                "fullname": row["fullname"],
                "role": row["role"],
                "sessionId": session_id,
                "jti": refresh_jti,
                "type": "refresh",
            },
            expires_delta=timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        )

        session_data = {
            "userId": row["id"],
            "refreshJti": refresh_jti,
        }

        await cache_service.set_by_key(
            f"session:{session_id}",
            REFRESH_TOKEN_TTL_SECONDS,
            session_data,
            redis_client,
        )

        user = user_from_row(row)

        return {
            "status": True,
            "message": "Login successful",
            "data": {
                "user": user,
                "access_token": token,
                "refresh_token": refresh_token,
            },
        }
    except Exception as e:
        logger.exception(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def refresh_tokens(refresh_token: str, redis_client: redis.Redis) -> dict:

    try:

        if not refresh_token:
            raise ValueError("Refresh token is required")

        if refresh_token.startswith("Bearer "):
            refresh_token = refresh_token[7:]

        payload = decode_access_token(refresh_token)

        if payload.get("type") != "refresh":
            raise ValueError("Invalid token type")

        if not payload.get("userId") or not payload.get("sessionId"):
            raise ValueError("Invalid token")

        session = await cache_service.get_by_key(
            f"session:{payload['sessionId']}", redis_client
        )
        if not session:
            raise ValueError("Invalid token")

        if session["refreshJti"] != payload.get("jti"):
            raise ValueError("Invalid token")

        new_access_token = create_token(
            {
                "userId": payload["userId"],
                "email": payload["email"],
                "fullname": payload["fullname"],
                "role": payload["role"],
                "sessionId": payload["sessionId"],
                "type": "auth",
            },
            expires_delta=timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        )

        new_jti = secrets.token_hex(16)

        new_refresh_token = create_token(
            {
                "userId": payload["userId"],
                "email": payload["email"],
                "fullname": payload["fullname"],
                "role": payload["role"],
                "sessionId": payload["sessionId"],
                "jti": new_jti,
                "type": "refresh",
            },
            expires_delta=timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        )

        session["refreshJti"] = new_jti

        await cache_service.set_by_key(
            f"session:{payload['sessionId']}",
            REFRESH_TOKEN_TTL_SECONDS,
            session,
            redis_client,
        )

        return {
            "status": True,
            "message": "Tokens refreshed",
            "data": {
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
            },
        }
    except ValueError as e:
        logger.error(f"Token refresh error: {e}")
        return {"status": False, "message": str(e), "data": {}}
    except Exception as e:
        logger.exception(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def logout(
    access_token: str | None, refresh_token: str | None, redis_client: redis.Redis
) -> dict:
    for token in (refresh_token, access_token):
        if not token:
            raise ValueError("Token is required")

        try:
            if token.startswith("Bearer "):
                token = token[7:]

            payload = decode_access_token(token)
            session_id = payload.get("sessionId")

            if not session_id:
                raise ValueError("Invalid session")

            await cache_service.delete_by_key(f"session:{session_id}", redis_client)
            return {"status": True, "message": "Logged out", "data": {}}
        except ValueError as e:
            logger.error(f"Token refresh error: {e}")
            return {"status": False, "message": str(e), "data": {}}
        except Exception as e:
            logger.exception(f"Token refresh error: {e}")
            return {"status": False, "message": "Invalid token", "data": {}}


async def forget_password(
    conn: asyncpg.Connection,
    redis_client,
    channel: aio_pika.abc.AbstractChannel,
    data: ForgetPasswordRequestModel,
) -> dict:
    try:
        row = await conn.fetchrow(
            "SELECT id, fullname, email, role FROM users WHERE email = $1", data.email
        )

        if not row:
            return {"status": False, "message": "User not found", "data": {}}

        code = generate_temp_code()
        cache_key = f"{row['id']}:{row['email']}"

        await cache_service.set_by_key(
            cache_key, RESET_CODE_REDIS_TTL, {"code": code}, redis_client
        )

        html = RESET_PASSWORD_EMAIL_TEMPLATE.replace("CODE_HERE", code)

        await messaging_service.publish(
            EMAIL_QUEUE,
            {
                "to": data.email,
                "from": settings.EMAIL_FROM,
                "subject": "Password reset code",
                "html": html,
                "message": "",
                "base64Attachment": "",
                "base64AttachmentName": "",
            },
            channel,
        )

        reset_payload = reset_jwt_payload(
            row["id"],
            row["email"],
            row["fullname"],
            row["role"],
            can_update=False,
        )
        token = create_token(
            reset_payload, expires_delta=timedelta(seconds=RESET_COOKIE_MAX_AGE)
        )

        return {
            "status": True,
            "message": "Verification code sent",
            "data": {"access_token": token},
        }
    except Exception as e:
        logger.exception(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def validate_reset_code(
    redis_client, user: dict, data: ValidateCodeRequest
) -> dict:
    try:
        cache_key = f"{user['userId']}:{user['email']}"
        redis_data = await cache_service.get_by_key(cache_key, redis_client)

        if not redis_data or redis_data.get("code") != data.code:
            return {"status": False, "message": "Invalid or expired code", "data": {}}

        await cache_service.delete_by_key(cache_key, redis_client)

        reset_payload = reset_jwt_payload(
            user["userId"],
            user["email"],
            user["fullname"],
            user["role"],
            can_update=True,
        )
        token = create_token(
            reset_payload, expires_delta=timedelta(seconds=RESET_COOKIE_MAX_AGE)
        )

        return {
            "status": True,
            "message": "Code validated",
            "data": {"access_token": token},
        }
    except Exception as e:
        logger.exception(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def update_password_after_reset(
    conn: asyncpg.Connection, user: dict, data: UpdatePasswordRequest
) -> dict:
    try:
        hashed = hash_password(data.password)

        row = await conn.fetchrow(
            """
            UPDATE users SET password = $1, updated_at = NOW()
            WHERE id = $2
            RETURNING id, fullname, email, role, created_at
            """,
            hashed,
            user["userId"],
        )

        if not row:
            return {"status": False, "message": "User not found", "data": {}}

        return {
            "status": True,
            "message": "Password updated successfully",
            "data": {"user": user_from_row(row)},
        }
    except Exception as e:
        logger.exception(e)
        return {"status": False, "message": "Internal server error", "data": {}}
