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

EVENT_TYPES = [
    "league_events",
    "wager_events",
    "lan_events",
    "xp_events"
]

PLATFORM_FEE_PERCENT = 5
REGISTRATION_STATUSES = ["pending", "paid", "payment_failed", "canceled", "expired"]

# Unpaid registrations hold their pass slot for this long, then the background sweeper releases it.
REGISTRATION_HOLD_MINUTES = 30
HOLD_SWEEP_INTERVAL_SECONDS = 120

STRIPE_LIVE_MODE = os.getenv("STRIPE_LIVE_MODE", "false").lower() == "true"

STRIPE_SECRET_KEY = os.getenv("STRIPE_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_LIVE_WEBHOOK_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_WEBHOOK_KEY")
