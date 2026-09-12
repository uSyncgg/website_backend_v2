import os

STRIPE_PAYMENT_METHOD_TYPES = ["card", "link", "cashapp"]
STRIPE_CURRENCY = "usd"

GAMES = [
    "Call of Duty",
    "CS2",
    "Halo",
    "League of Legends",
    "Valorant",
    "Rocket League",
    "Warzone"
]

PLATFORM_FEE_PERCENT = 5
REGISTRATION_STATUSES = ["pending", "paid", "payment_failed", "canceled"]

STRIPE_LIVE_MODE = os.getenv("STRIPE_LIVE_MODE", "false").lower() == "true"

STRIPE_SECRET_KEY = os.getenv("STRIPE_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_LIVE_WEBHOOK_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_WEBHOOK_KEY")
