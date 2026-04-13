import inspect
import secrets
import string
from typing import Callable

from fastapi.responses import JSONResponse


async def default_response(callable_function: Callable, params: list = [], is_creation: bool = False,
                           dict_response: bool = False):
    try:
        if is_async_callable(callable_function):
            result = await callable_function(*params)
        else:
            result = callable_function(*params)
        if not result["status"]:
            if not dict_response:
                return JSONResponse(status_code=400, content={"detail": result["message"]})
            return {"status": False, "message": result["message"], "data": {}}

        status_code = 200 if not is_creation else 201
        if not dict_response:
            return JSONResponse(status_code=status_code, content={"message": result["message"], "data": result["data"]})
        return {"status": True, "message": result["message"], "data": result["data"]}
    except Exception as e:
        logger.exception(e)
        if not dict_response:
            return JSONResponse(status_code=500, content={"detail": "Erro interno com o servidor."})
        return {"status": False, "message": "Erro interno com o servidor.", "data": {}}


def serialize_row(
        row: dict,
        date_fields: list[str] | None = None,
        decimal_fields: list[str] | None = None,
) -> dict:
    date_fields = date_fields or []
    decimal_fields = decimal_fields or []

    result = dict(row)

    for f in date_fields:
        if result.get(f) is not None:
            result[f] = str(result[f])

    for f in decimal_fields:
        if result.get(f) is not None:
            result[f] = float(result[f])

    return result


def generate_temp_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))

def is_async_callable(fn: Callable) -> bool:
    return inspect.iscoroutinefunction(fn)
