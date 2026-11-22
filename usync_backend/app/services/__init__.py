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

# Wallet deposits: at least $5, plus a 10% fee on top, and at most $1,000 in any 24 hours (pending
# and succeeded deposits both count). Unpaid deposits are canceled after DEPOSIT_HOLD_MINUTES.
DEPOSIT_MIN_CENTS = 500
DEPOSIT_FEE_PERCENT = 10
DEPOSIT_DAILY_LIMIT_CENTS = 100_000
DEPOSIT_HOLD_MINUTES = 30
# Withdrawals: any amount of Earnings from $1, or all of them (the only option below $1).
WITHDRAWAL_MIN_CENTS = 100
# Where manual credits/debits, deposit refunds and withdrawal requests are reported (each one is also in the audit log).
MONEY_ALERT_EMAIL = os.getenv("MONEY_ALERT_EMAIL", "contact@usync.gg")
REGISTRATION_STATUSES = ["pending", "paid", "payment_failed", "canceled", "expired", "refunded"]

# Unpaid registrations hold their pass slot for this long, then the background sweeper releases it.
REGISTRATION_HOLD_MINUTES = 30
HOLD_SWEEP_INTERVAL_SECONDS = 120

STRIPE_LIVE_MODE = os.getenv("STRIPE_LIVE_MODE", "false").lower() == "true"

STRIPE_SECRET_KEY = os.getenv("STRIPE_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_LIVE_WEBHOOK_KEY") if STRIPE_LIVE_MODE else os.getenv("STRIPE_TEST_WEBHOOK_KEY")
