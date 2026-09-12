import os
import resend
import uuid
from fastapi_mail import FastMail, MessageSchema, MessageType, ConnectionConfig

resend_api_key = os.getenv("RESEND_API_KEY")
FROM_EMAIL = "contact@usync.gg"

async def send_receipt_email(to: str, display_name: str, total_snapshot_cents: int, event_name: str, id: uuid.UUID) -> None:
    total_dollars = total_snapshot_cents / 100

    html = f"""
    <html>
    <body style="font-family: sans-serif; color: #111111;">
        <h2>You're registered!</h2>
        <p>Hi {display_name},</p>
        <p>Your payment of <strong>${total_dollars:.2f}</strong> was received and your registration is confirmed for {event_name}.</p>
        <p>Your Receipt ID should you have any issue is ${id}</p>
        <p>Thanks for signing up on usync.gg.</p>
    </body>
    </html>
    """

    await resend.Emails.send_async({
        "from": FROM_EMAIL,
        "to": [to],
        "subject": "Your registration is confirmed",
        "html": html,
    })

