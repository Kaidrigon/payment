import os
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from uuid import uuid4

import razorpay
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.database import database, client
from app.providers.razorpay import create_payment_order


load_dotenv()

logger = logging.getLogger(__name__)

key_id = os.getenv("RAZORPAY_KEY_ID")
key_secret = os.getenv("RAZORPAY_KEY_SECRET")

if not key_id or not key_secret:
    raise RuntimeError("Razorpay API credentials are not configured")

client_razorpay = razorpay.Client(auth=(key_id, key_secret))

router = APIRouter(
    prefix="/api/payments",
    tags=["Payments"],
)


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def checkout_response(payment_record):
    return {
        "razorpay_order_id": payment_record["provider_order_id"],
        "amount": payment_record["amount"],
        "currency": payment_record["currency"],
        "key_id": key_id,
    }


async def get_existing_active_attempt(order):
    attempt_id = order.get("active_payment_attempt_id")

    if not attempt_id:
        return None

    payment_record = await database.payments.find_one({
        "attempt_id": attempt_id,
        "order_id": order["order_id"],
        "provider": "razorpay",
    })

    if payment_record and payment_record["status"] == "created":
        return payment_record

    # A failed or missing attempt must not permanently block retries.
    if not payment_record or payment_record["status"] == "failed":
        await database.orders.update_one(
            {
                "order_id": order["order_id"],
                "status": "pending",
                "active_payment_attempt_id": attempt_id,
            },
            {
                "$unset": {
                    "active_payment_attempt_id": "",
                    "payment_attempt_started_at": "",
                }
            },
        )

    return None


@router.post("/create/{order_id}")
async def create_payment(order_id: str):
    order = await database.orders.find_one({
        "order_id": order_id,
    })

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    if order["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail="Order is not available for payment",
        )

    # Reuse an existing active attempt instead of creating another one.
    existing_attempt = await get_existing_active_attempt(order)

    if existing_attempt:
        return checkout_response(existing_attempt)

    # Atomically reserve this order for one payment attempt.
    attempt_id = str(uuid4())
    now = datetime.now(timezone.utc)

    reserved_order = await database.orders.find_one_and_update(
        {
            "order_id": order_id,
            "status": "pending",
            "active_payment_attempt_id": {"$exists": False},
        },
        {
            "$inc": {"payment_attempt_count": 1},
            "$set": {
                "active_payment_attempt_id": attempt_id,
                "payment_attempt_started_at": now,
            },
        },
        return_document=True,
    )

    if reserved_order is None:
        # Another request may have reserved the order just now.
        latest_order = await database.orders.find_one({
            "order_id": order_id,
        })

        if latest_order is None:
            raise HTTPException(
                status_code=404,
                detail="Order not found",
            )

        if latest_order["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Order is not available for payment",
            )

        existing_attempt = await get_existing_active_attempt(latest_order)

        if existing_attempt:
            return checkout_response(existing_attempt)

        raise HTTPException(
            status_code=409,
            detail="Payment attempt is being prepared. Please retry shortly.",
        )

    attempt_number = reserved_order["payment_attempt_count"]

    try:
        # Use a distinct receipt for each provider-side attempt.
        provider_order = await create_payment_order(
            order_id=f"{order_id}-attempt-{attempt_number}",
            amount=reserved_order["amount"],
        )

        payment_record = {
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "order_id": order_id,
            "provider": "razorpay",
            "provider_order_id": provider_order["id"],
            "amount": reserved_order["amount"],
            "currency": reserved_order["currency"],
            "status": "created",
            "created_at": datetime.now(timezone.utc),
        }

        await database.payments.insert_one(payment_record)

    except Exception:
        logger.exception("Could not create or save payment attempt")

        # Release the reservation so the customer can retry.
        await database.orders.update_one(
            {
                "order_id": order_id,
                "active_payment_attempt_id": attempt_id,
                "status": "pending",
            },
            {
                "$unset": {
                    "active_payment_attempt_id": "",
                    "payment_attempt_started_at": "",
                }
            },
        )

        raise HTTPException(
            status_code=502,
            detail="Could not prepare payment. Please try again.",
        )

    return checkout_response(payment_record)


async def mark_attempt_failed(payment_record, provider_payment_id):
    now = datetime.now(timezone.utc)

    async with await client.start_session() as session:
        async with session.start_transaction():
            current_payment = await database.payments.find_one(
                {"_id": payment_record["_id"]},
                session=session,
            )

            if current_payment is None:
                raise RuntimeError("Payment attempt record disappeared")

            # Never downgrade a captured payment to failed.
            if current_payment["status"] == "captured":
                return "captured"

            if current_payment["status"] == "created":
                await database.payments.update_one(
                    {
                        "_id": current_payment["_id"],
                        "status": "created",
                    },
                    {
                        "$set": {
                            "status": "failed",
                            "provider_payment_id": provider_payment_id,
                            "updated_at": now,
                        }
                    },
                    session=session,
                )

            # Release only this attempt's reservation.
            await database.orders.update_one(
                {
                    "order_id": payment_record["order_id"],
                    "active_payment_attempt_id": payment_record["attempt_id"],
                    "status": "pending",
                },
                {
                    "$unset": {
                        "active_payment_attempt_id": "",
                        "payment_attempt_started_at": "",
                    }
                },
                session=session,
            )

    return "failed"


async def mark_attempt_captured(payment_record, provider_payment_id):
    now = datetime.now(timezone.utc)
    duplicate_capture = False

    async with await client.start_session() as session:
        async with session.start_transaction():
            current_payment = await database.payments.find_one(
                {"_id": payment_record["_id"]},
                session=session,
            )

            order = await database.orders.find_one(
                {"order_id": payment_record["order_id"]},
                session=session,
            )

            if current_payment is None or order is None:
                raise RuntimeError("Payment or order record not found")

            if current_payment["status"] == "captured":
                # A different payment ID for the same attempt may indicate
                # an additional successful charge. Preserve the original ID.
                if current_payment.get("provider_payment_id") != provider_payment_id:
                    duplicate_capture = True

                    await database.payments.update_one(
                        {"_id": current_payment["_id"]},
                        {
                            "$set": {"duplicate_capture": True},
                            "$addToSet": {
                                "additional_provider_payment_ids": provider_payment_id
                            },
                        },
                        session=session,
                    )

            else:
                await database.payments.update_one(
                    {"_id": current_payment["_id"]},
                    {
                        "$set": {
                            "status": "captured",
                            "provider_payment_id": provider_payment_id,
                            "captured_at": now,
                        }
                    },
                    session=session,
                )

            if order["status"] == "pending":
                await database.orders.update_one(
                    {
                        "order_id": order["order_id"],
                        "status": "pending",
                    },
                    {
                        "$set": {
                            "status": "paid",
                            "paid_at": now,
                            "paid_attempt_id": payment_record["attempt_id"],
                        },
                        "$unset": {
                            "active_payment_attempt_id": "",
                            "payment_attempt_started_at": "",
                        },
                    },
                    session=session,
                )

            elif order.get("paid_attempt_id") != payment_record["attempt_id"]:
                # Another attempt already paid this order. Record the extra
                # capture for review rather than silently treating it as normal.
                duplicate_capture = True

                await database.payments.update_one(
                    {"_id": current_payment["_id"]},
                    {"$set": {"duplicate_capture": True}},
                    session=session,
                )

    return {
        "order_marked_paid": True,
        "duplicate_capture": duplicate_capture,
    }


@router.post("/verify")
async def verify_payment(request: VerifyPaymentRequest):
    payment_record = await database.payments.find_one({
        "provider": "razorpay",
        "provider_order_id": request.razorpay_order_id,
    })

    if payment_record is None:
        raise HTTPException(
            status_code=404,
            detail="Payment order not found",
        )

    try:
        client_razorpay.utility.verify_payment_signature({
            "razorpay_order_id": request.razorpay_order_id,
            "razorpay_payment_id": request.razorpay_payment_id,
            "razorpay_signature": request.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid payment signature",
        )

    try:
        payment_details = client_razorpay.payment.fetch(
            request.razorpay_payment_id
        )
    except Exception:
        logger.exception("Could not fetch Razorpay payment")
        raise HTTPException(
            status_code=502,
            detail="Could not confirm payment status",
        )

    if payment_details.get("order_id") != payment_record["provider_order_id"]:
        raise HTTPException(
            status_code=400,
            detail="Payment does not match the stored order",
        )

    if (
        payment_details.get("amount") != payment_record["amount"]
        or payment_details.get("currency") != payment_record["currency"]
    ):
        raise HTTPException(
            status_code=400,
            detail="Payment amount or currency mismatch",
        )

    provider_status = payment_details.get("status")

    if provider_status == "captured":
        try:
            result = await mark_attempt_captured(
                payment_record,
                request.razorpay_payment_id,
            )
        except Exception:
            logger.exception("Could not update captured payment")
            raise HTTPException(
                status_code=503,
                detail="Payment was confirmed but database update failed. Please retry.",
            )

        return {
            "verified": True,
            "payment_status": "captured",
            **result,
        }

    if provider_status == "failed":
        try:
            await mark_attempt_failed(
                payment_record,
                request.razorpay_payment_id,
            )
        except Exception:
            logger.exception("Could not update failed payment")
            raise HTTPException(
                status_code=503,
                detail="Could not update payment status. Please retry.",
            )

    return {
        "verified": True,
        "payment_status": provider_status,
        "order_marked_paid": False,
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    body = await request.body()

    signature = request.headers.get("X-Razorpay-Signature")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    if not secret or not signature:
        raise HTTPException(
            status_code=400,
            detail="Missing webhook secret or signature",
        )

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook signature",
        )

    try:
        event_data = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    event = event_data.get("event")
    payload = event_data.get("payload", {})
    payment_data = payload.get("payment", {}).get("entity", {})

    if event not in ("payment.captured", "payment.failed"):
        return {"received": True, "ignored": True}

    provider_order_id = payment_data.get("order_id")
    provider_payment_id = payment_data.get("id")

    if not provider_order_id or not provider_payment_id:
        raise HTTPException(
            status_code=400,
            detail="Missing payment or order ID",
        )

    payment_record = await database.payments.find_one({
        "provider": "razorpay",
        "provider_order_id": provider_order_id,
    })

    if payment_record is None:
        # Return an error so the provider can retry delivery.
        raise HTTPException(
            status_code=500,
            detail="Payment record not found",
        )

    if (
        payment_data.get("amount") != payment_record["amount"]
        or payment_data.get("currency") != payment_record["currency"]
    ):
        raise HTTPException(
            status_code=400,
            detail="Payment amount or currency mismatch",
        )

    try:
        if event == "payment.failed":
            status = await mark_attempt_failed(
                payment_record,
                provider_payment_id,
            )

            return {
                "received": True,
                "payment_status": status,
            }

        result = await mark_attempt_captured(
            payment_record,
            provider_payment_id,
        )

        return {
            "received": True,
            "payment_status": "captured",
            **result,
        }

    except Exception:
        # A 5xx response allows Razorpay to retry the webhook.
        logger.exception("Could not process Razorpay webhook")
        raise HTTPException(
            status_code=500,
            detail="Could not process webhook; please retry",
        )
