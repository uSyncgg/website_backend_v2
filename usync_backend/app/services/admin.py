from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Read-only queries behind the admin dashboard. Every function here only SELECTs and runs
# on get_readonly_db, whose transaction Postgres keeps READ ONLY.

# The one definition of an account's roles and completeness, shared by the Overview totals and
# the Accounts tab so the two always agree. Starts from auth.users (every sign-up; users has no
# created_at and only exists once sign-up finishes). A role counts only when its profile has
# its player/host/venue details. Unfinished means no users row, no profile, or a profile
# missing its details.
ACCOUNT_SUMMARY_CTES = """
    profile_summary AS (
        SELECT p.account_id,
               bool_or(p.type = 'player' AND pl.profile_id IS NOT NULL) AS is_player,
               bool_or(p.type = 'host' AND h.profile_id IS NOT NULL) AS is_host,
               bool_or(p.type = 'venue' AND v.profile_id IS NOT NULL) AS is_venue,
               bool_and(CASE p.type
                   WHEN 'player' THEN pl.profile_id IS NOT NULL
                   WHEN 'host' THEN h.profile_id IS NOT NULL
                   ELSE v.profile_id IS NOT NULL
               END) AS all_complete
        FROM profiles p
        LEFT JOIN players pl ON pl.profile_id = p.id
        LEFT JOIN hosts h ON h.profile_id = p.id
        LEFT JOIN venues v ON v.profile_id = p.id
        GROUP BY p.account_id
    ),
    account_summary AS (
        SELECT a.id, a.created_at, coalesce(u.email, a.email) AS email, u.default_profile_id,
               coalesce(s.is_player, false) AS is_player,
               coalesce(s.is_host, false) AS is_host,
               coalesce(s.is_venue, false) AS is_venue,
               (u.id IS NOT NULL AND coalesce(s.all_complete, false)) AS profile_complete
        FROM auth.users a
        LEFT JOIN users u ON u.id = a.id
        LEFT JOIN profile_summary s ON s.account_id = a.id
    )
"""

SIGNUPS_SQL = text(f"""
    WITH {ACCOUNT_SUMMARY_CTES}
    SELECT (created_at AT TIME ZONE 'America/New_York')::date AS date,
           count(*) AS total,
           count(*) FILTER (WHERE NOT profile_complete) AS unfinished,
           count(*) FILTER (WHERE is_player) AS players,
           count(*) FILTER (WHERE is_host) AS hosts,
           count(*) FILTER (WHERE is_venue) AS venues
    FROM account_summary
    GROUP BY 1
    ORDER BY 1
""")

# How many profiles list each game right now. DISTINCT counts a profile once per game even if
# its array repeats one.
GAMES_SQL = text("""
    SELECT 'players' AS audience, g AS game, count(DISTINCT pl.profile_id) AS n
    FROM players pl, unnest(pl.games) AS g
    GROUP BY g
    UNION ALL
    SELECT 'hosts', g, count(DISTINCT h.profile_id)
    FROM hosts h, unnest(h.games) AS g
    GROUP BY g
""")

# Each CTE below returns one row per account, so joining them can't multiply rows. Every kind of
# data is grouped in one pass instead of being looked up separately for each account.
# Links merge the profile_links rows and the player profile's game platforms.
ACCOUNTS_SQL = text(f"""
    WITH {ACCOUNT_SUMMARY_CTES},
    first_profile AS (
        SELECT DISTINCT ON (account_id) account_id, username
        FROM profiles
        ORDER BY account_id, created_at
    ),
    game_summary AS (
        SELECT account_id, array_agg(DISTINCT game) AS games
        FROM (
            SELECT p.account_id, g AS game
            FROM profiles p JOIN players pl ON pl.profile_id = p.id, unnest(pl.games) AS g
            UNION ALL
            SELECT p.account_id, g
            FROM profiles p JOIN hosts h ON h.profile_id = p.id, unnest(h.games) AS g
        ) all_games
        GROUP BY account_id
    ),
    link_summary AS (
        SELECT account_id, jsonb_object_agg(platform, handle) AS linked
        FROM (
            SELECT DISTINCT ON (account_id, platform) account_id, platform, handle
            FROM (
                SELECT l.account_id, l.platform::text AS platform, l.handle FROM profile_links l
                UNION ALL
                SELECT p.account_id, v.platform, v.handle
                FROM profiles p
                JOIN players pl ON pl.profile_id = p.id
                CROSS JOIN LATERAL (VALUES ('battlenet', pl.battlenet), ('activision', pl.activision),
                                           ('steam', pl.steam), ('riot', pl.riot)) AS v(platform, handle)
                WHERE v.handle IS NOT NULL AND v.handle <> ''
            ) all_links
            ORDER BY account_id, platform
        ) one_per_platform
        GROUP BY account_id
    )
    SELECT s.id, s.created_at, s.email,
           coalesce(dp.username, f.username) AS username,
           s.is_player, s.is_host, s.is_venue, s.profile_complete,
           coalesce(g.games, '{{}}') AS games,
           coalesce(l.linked, '{{}}'::jsonb) AS linked
    FROM account_summary s
    LEFT JOIN profiles dp ON dp.id = s.default_profile_id
    LEFT JOIN first_profile f ON f.account_id = s.id
    LEFT JOIN game_summary g ON g.account_id = s.id
    LEFT JOIN link_summary l ON l.account_id = s.id
    ORDER BY s.created_at DESC
""")

# Every listing across the event tables, most recently updated first.
EVENTS_SQL = text("""
    SELECT c.id, c.name, c.event_type, c.game, c.path, c.status, c.verified, c.updated_at,
           (e.user_id IS NOT NULL) AS host_owned
    FROM (
        SELECT event_id AS id, name, 'LAN' AS event_type, game, path, status, verified, updated_at FROM lan_events WHERE NOT archived
        UNION ALL
        SELECT id, name, 'LEAGUE_GROUP', game, path, status, verified, updated_at FROM league_parent_events
        UNION ALL
        SELECT event_id, name, 'LEAGUE', game, path, status, verified, updated_at FROM league_events
        UNION ALL
        SELECT event_id, name, 'WAGER', game, path, status, verified, updated_at FROM wager_events
        UNION ALL
        SELECT event_id, name, 'XP', game, path, status, verified, updated_at FROM xp_events
    ) c
    LEFT JOIN events e ON e.id = c.id
    ORDER BY c.updated_at DESC
""")

async def get_admin_analytics(db: AsyncSession) -> dict:
    """
    Account totals, sign-ups per day, and the games listed on profiles.

    ::param db the read-only database session

    ::return a dict matching AdminAnalyticsOut
    """

    signups = [dict(row) for row in (await db.execute(SIGNUPS_SQL)).mappings()]
    totals = {key: sum(day[key] for day in signups) for key in ("total", "unfinished", "players", "hosts", "venues")}

    games = {"players": {}, "hosts": {}}
    for row in (await db.execute(GAMES_SQL)).mappings():
        games[row["audience"]][row["game"]] = row["n"]

    return {"generated_at": datetime.now(timezone.utc), "totals": totals, "signups": signups, "games": games}

async def get_admin_events(db: AsyncSession) -> dict:
    """
    Every event listing with when it was last updated.

    ::param db the read-only database session

    ::return a dict matching AdminEventsOut
    """

    events = [dict(row) for row in (await db.execute(EVENTS_SQL)).mappings()]

    return {"generated_at": datetime.now(timezone.utc), "events": events}

async def get_admin_accounts(db: AsyncSession) -> dict:
    """
    Every account with its roles, profile status, games and linked platforms.

    ::param db the read-only database session

    ::return a dict matching AdminAccountsOut
    """

    accounts = [dict(row) for row in (await db.execute(ACCOUNTS_SQL)).mappings()]

    return {"generated_at": datetime.now(timezone.utc), "accounts": accounts}
