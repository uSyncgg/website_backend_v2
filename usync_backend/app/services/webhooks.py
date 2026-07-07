import stripe
import os

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

async def handle_stripe_webhook(payload: bytes, signature: str):
    """
    Stripe webhook handler.

    ::param payload the byte payload from the frontend
    ::param signature the signature header from the frontend
    """

    stripe.Webhook.construct_event(payload, signature, os.getenv("STRIPE_TEST_WEBHOOK_KEY"))