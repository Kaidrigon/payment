
import asyncio
from app.database import client, connect_database, close_database


async def main():
    try:
        await connect_database()

        result = await client.admin.command("hello")

        print("Replica set:", result.get("setName", "Not reported"))
        print("Mongos:", result.get("msg") == "isdbgrid")
        print(
            "Transactions likely supported:",
            bool(result.get("setName") or result.get("msg") == "isdbgrid"),
        )
    finally:
        await close_database()


asyncio.run(main())
