import os

import razorpay
from dotenv import load_dotenv


load_dotenv()


key_id = os.getenv("RAZORPAY_KEY_ID")
key_secret = os.getenv("RAZORPAY_KEY_SECRET")


client = razorpay.Client(
    auth=(key_id, key_secret)
)


async def create_payment_order(
    order_id: str,
    amount: int,
):
    payment_order = client.order.create(
        data={
            "amount": amount,
            "currency": "INR",
            "receipt": order_id,
        }
    )

    return payment_order
