from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Read-only queries behind the admin dashboard. Every function here only SELECTs and runs
# on get_readonly_db, whose transaction Postgres keeps READ ONLY.

# Sign-up date comes from Supabase's auth.users, since the users table has no created_at.
SIGNUPS_SQL = text("""
    SELECT (a.created_at AT TIME ZONE 'America/New_York')::date AS date,
           count(*) AS total,
           count(*) FILTER (WHERE u.is_player) AS players,
           count(*) FILTER (WHERE u.is_host) AS hosts,
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM venues v WHERE v.id = u.id)) AS venues
    FROM users u
    JOIN auth.users a ON a.id = u.id
    GROUP BY 1
    ORDER BY 1
""")

GAMES_SQL = text("""
    SELECT 'players' AS audience, g AS game, count(*) AS n FROM players, unnest(games) AS g GROUP BY g
    UNION ALL
    SELECT 'hosts', g, count(*) FROM hosts, unnest(games) AS g GROUP BY g
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
    Account totals, sign-ups per day, and games picked at sign-up.

    ::param db the read-only database session

    ::return a dict matching AdminAnalyticsOut
    """

    signups = [dict(row) for row in (await db.execute(SIGNUPS_SQL)).mappings()]
    totals = {key: sum(day[key] for day in signups) for key in ("total", "players", "hosts", "venues")}

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
