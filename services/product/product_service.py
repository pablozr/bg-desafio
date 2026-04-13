from typing import Optional

import asyncpg

from core.logger.logger import logger
from functions.utils.utils import serialize_row

from schemas.product import ProductData, ProductFilters


async def create_product(conn: asyncpg.Connection, data: ProductData) -> dict:
    query = """
            INSERT INTO products (name, description, price, quantity, active)
            VALUES ($1, $2, $3, $4, $5) RETURNING id, name, description, price, quantity, active, created_at
            """

    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                query,
                data["name"],
                data["description"],
                data["price"],
                data["quantity"],
                data["active"],
            )

            response = serialize_row(
                {**row}, date_fields=["created_at"], decimal_fields=["price"]
            )

            return {
                "status": True,
                "message": "Product created successfully",
                "data": response,
            }
    except Exception as e:
        logger.error(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def get_products(
    conn: asyncpg.Connection, filters: Optional[ProductFilters]
) -> dict:
    try:
        query = """
                SELECT id,
                       name,
                       description,
                       price,
                       quantity,
                       active,
                       created_at,
                       updated_at
                FROM products
                """

        where_clauses = []
        values = []

        if filters:
            data = filters.model_dump(exclude_none=True)

            if "name" in data:
                where_clauses.append("name ILIKE $%d" % (len(values) + 1))
                values.append(f"%{data['name']}%")

            if "active" in data:
                where_clauses.append("active = $%d" % (len(values) + 1))
                values.append(data["active"])

            if "min_price" in data:
                where_clauses.append("price >= $%d" % (len(values) + 1))
                values.append(data["min_price"])

            if "max_price" in data:
                where_clauses.append("price <= $%d" % (len(values) + 1))
                values.append(data["max_price"])

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY created_at DESC"

        if filters and filters.limit is not None:
            query += " LIMIT $%d" % (len(values) + 1)
            values.append(filters.limit)

        if filters and filters.offset is not None:
            query += " OFFSET $%d" % (len(values) + 1)
            values.append(filters.offset)

        rows = await conn.fetch(query, *values)

        response = [
            serialize_row(
                {**row},
                date_fields=["created_at", "updated_at"],
                decimal_fields=["price"],
            )
            for row in rows
        ]

        return {
            "status": True,
            "message": "Products retrieved successfully",
            "data": response,
        }
    except Exception as e:
        logger.error(e)
        return {"status": False, "message": "Internal server error", "data": []}


async def get_product_by_id(conn: asyncpg.Connection, product_id: int) -> dict:
    try:
        row = await conn.fetchrow(
            """
            SELECT id, name, description, price, quantity, active, created_at, updated_at
            FROM products
            WHERE id = $1
            """,
            product_id,
        )

        if not row:
            return {"status": False, "message": "Product not found", "data": {}}

        response = serialize_row(
            {**row}, date_fields=["created_at", "updated_at"], decimal_fields=["price"]
        )

        return {
            "status": True,
            "message": "Product retrieved successfully",
            "data": response,
        }
    except Exception as e:
        logger.error(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def update_product(conn: asyncpg.Connection, product_id: int, data: dict) -> dict:
    allowed_columns = ["name", "description", "price", "quantity", "active"]
    filtered = {key: value for key, value in data.items() if key in allowed_columns}

    if not filtered:
        return {"status": False, "message": "No valid fields to update", "data": {}}

    columns = list(filtered.keys())
    values = list(filtered.values())

    set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(columns))
    set_clause += ", updated_at = NOW()"

    query = f"""
            UPDATE products
            SET {set_clause}
            WHERE id = $1
            RETURNING id, name, description, price, quantity, active, created_at, updated_at
        """

    try:
        async with conn.transaction():
            row = await conn.fetchrow(query, product_id, *values)

            if not row:
                return {"status": False, "message": "Product not found", "data": {}}

            response = serialize_row(
                {**row},
                date_fields=["created_at", "updated_at"],
                decimal_fields=["price"],
            )

            return {
                "status": True,
                "message": "Product updated successfully",
                "data": response,
            }
    except Exception as e:
        logger.error(e)
        return {"status": False, "message": "Internal server error", "data": {}}


async def delete_product(conn: asyncpg.Connection, product_id: int) -> dict:
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE products
                SET active = FALSE, updated_at = NOW()
                WHERE id = $1
                RETURNING id, name, description, price, quantity, active, created_at, updated_at
                """,
                product_id,
            )

            if not row:
                return {"status": False, "message": "Product not found", "data": {}}

            response = serialize_row(
                {**row},
                date_fields=["created_at", "updated_at"],
                decimal_fields=["price"],
            )

            return {
                "status": True,
                "message": "Product deleted successfully",
                "data": response,
            }

    except Exception as e:
        logger.error(e)
        return {"status": False, "message": "Internal server error", "data": {}}
