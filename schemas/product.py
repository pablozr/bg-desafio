from typing import TypedDict

from pydantic import BaseModel, Field
from decimal import Decimal


class CreateProductRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(gt=0)
    quantity: int = Field(ge=0)
    active: bool


class CreateProductResponse(TypedDict):
    id: int
    name: str
    description: str
    price: float
    quantity: int
    active: bool
    created_at: str
    updated_at: str
