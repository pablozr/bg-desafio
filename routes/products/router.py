

import asyncpg
from fastapi import APIRouter, Depends, Query

from core.postgresql.postgresql import postgresql
from core.security.security import validate_token_wrapper
from functions.utils.utils import default_response
from schemas.product import CreateProductRequest, ProductFilters, UpdateProductRequest
from services.product import product_service

router = APIRouter()


@router.post("", dependencies=[Depends(validate_token_wrapper)])
async def create_product(
    data: CreateProductRequest,
    conn: asyncpg.Connection = Depends(postgresql.get_db),
) -> dict:
    return await default_response(
        product_service.create_product,
        [conn, data.model_dump()],
        is_creation=True,
    )


@router.patch("/{id}", dependencies=[Depends(validate_token_wrapper)])
async def update_product(
    data: UpdateProductRequest,
    id: int,
    conn: asyncpg.Connection = Depends(postgresql.get_db),
) -> dict:
    return await default_response(
        product_service.update_product,
        [conn, id, data.model_dump(exclude_none=True)],
    )


@router.get("", dependencies=[Depends(validate_token_wrapper)])
async def get_products(
    filters: ProductFilters = Query(),
    conn: asyncpg.Connection = Depends(postgresql.get_db),
) -> dict:
    return await default_response(product_service.get_products, [conn, filters])


@router.get("/{id}", dependencies=[Depends(validate_token_wrapper)])
async def get_product_by_id(
    id: int,
    conn: asyncpg.Connection = Depends(postgresql.get_db),
) -> dict:
    return await default_response(product_service.get_product_by_id, [conn, id])


@router.delete("/{id}", dependencies=[Depends(validate_token_wrapper)])
async def delete_product(
    id: int,
    conn: asyncpg.Connection = Depends(postgresql.get_db),
) -> dict:
    return await default_response(product_service.delete_product, [conn, id])
