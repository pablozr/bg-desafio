from typing import TypedDict, Optional

from pydantic import BaseModel, Field
from decimal import Decimal


class CreateProductRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(gt=0)
    quantity: int = Field(ge=0)
    active: bool


class UpdateProductRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, min_length=1, max_length=255)
    price: Optional[Decimal] = Field(None, gt=0)
    quantity: Optional[int] = Field(None, ge=0)
    active: Optional[bool] = None


class ProductFilters(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    limit: Optional[int] = Field(ge=1, default=20, le=100 )
    offset: Optional[int] = Field(ge=0, default=0)

class CreateProductResponse(TypedDict):
    id: int
    name: str
    description: str
    price: Decimal
    quantity: int
    active: bool
    created_at: str
    updated_at: str


class ProductData(TypedDict):
    name: str
    description: str
    price: Decimal
    quantity: int
    active: bool
