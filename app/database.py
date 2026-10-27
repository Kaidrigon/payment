
from pymongo import AsyncMongoClient

from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")

DATABASE_NAME = os.getenv(
    "MONGODB_DATABASE",
    "payment_system"
)

client = AsyncMongoClient(MONGODB_URL)

database = client[DATABASE_NAME]


async def connect_database():
    await client.admin.command("ping")
    print("MongoDB connected successfully!")


async def close_database():
    await client.close()
