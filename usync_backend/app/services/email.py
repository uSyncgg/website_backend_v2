import os
from fastapi_mail import FastMail, MessageSchema, MessageType, ConnectionConfig

conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("ZOHO_EMAIL"),
    MAIL_PASSWORD=os.getenv("ZOHO_PASSWORD"),
    MAIL_FROM=os.getenv("ZOHO_EMAIL"),
    MAIL_FROM_NAME="usync.gg",
    MAIL_PORT=587,
    MAIL_SERVER="smtp.zoho.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)

fm = FastMail(conf)

async def send_receipt_email(to: str, display_name: str, total_snapshot_cents: int, event_name: str) -> None:
    total_dollars = total_snapshot_cents / 100

    html = f"""
    <html>
    <body style="font-family: sans-serif; color: #111111;">
        <h2>You're registered!</h2>
        <p>Hi {display_name},</p>
        <p>Your payment of <strong>${total_dollars:.2f}</strong> was received and your registration is confirmed for {event_name}.</p>
        <p>Thanks for signing up on usync.gg.</p>
    </body>
    </html>
    """

    message = MessageSchema(
        subject="Your registration is confirmed",
        recipients=[to],
        body=html,
        subtype=MessageType.html,
    )

    await fm.send_message(message)
