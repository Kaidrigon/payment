
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import database
from app.catalog import PRODUCTS

router = APIRouter(prefix="/api/orders", tags=["Orders"])


class OrderItem(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=100)


class CreateOrderRequest(BaseModel):
    items: list[OrderItem] = Field(min_length=1, max_length=50)


@router.post("/")
async def create_order(request: CreateOrderRequest):
    order_items = []
    total_paise = 0

    for item in request.items:
        product = PRODUCTS.get(item.product_id)

        if product is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown product: {item.product_id}",
            )

        line_total = product["amount_paise"] * item.quantity
        total_paise += line_total

        order_items.append({
            "product_id": item.product_id,
            "product_name": product["name"],
            "unit_amount_paise": product["amount_paise"],
            "quantity": item.quantity,
            "line_total_paise": line_total,
        })

    order_id = str(uuid4())

    order = {
        "order_id": order_id,
        "items": order_items,
        "amount": total_paise,  # Integer paise for payment providers
        "currency": "INR",
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }

    await database.orders.insert_one(order)

    return {
        "message": "Order created successfully",
        "order_id": order_id,
        "amount_paise": total_paise,
        "currency": "INR",
        "status": "pending",
    }


@router.get("/{order_id}")
async def get_order(order_id: str):
    order = await database.orders.find_one(
        {"order_id": order_id},
        {"_id": 0},
    )

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    return order
