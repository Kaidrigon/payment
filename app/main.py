from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import (
    connect_database,
    close_database,
    database,
)
from app.orders import router as orders_router
from app.payments import router as payments_router


async def create_database_indexes():
    await database.orders.create_index(
        "order_id",
        unique=True,
        name="unique_order_id",
    )

    await database.payments.create_index(
        [
            ("provider", 1),
            ("provider_order_id", 1),
        ],
        unique=True,
        name="unique_provider_order",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_database()
    await create_database_indexes()

    yield

    await close_database()


app = FastAPI(
    title="Payment System",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://2fe6-2409-40d6-415-785e-19fe-6ac9-8f63-a58.ngrok-free.app",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(orders_router)
app.include_router(payments_router)


@app.get("/")
async def home():
    return {
        "message": "Payment system is running!"
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy"
    }


@app.get("/database-test")
async def database_test():
    result = await database.command("ping")

    return {
        "database": "connected",
        "result": result["ok"],
    }
