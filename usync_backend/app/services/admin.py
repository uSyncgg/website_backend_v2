from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Read-only queries behind the admin dashboard. Every function here only SELECTs and runs
# on get_readonly_db, whose transaction Postgres keeps READ ONLY.

# One account (users) can have a player, host and venue profile (profiles.type), each with its
# own detail row. Sign-up date comes from Supabase's auth.users, since users has no created_at.
SIGNUPS_SQL = text("""
    SELECT (a.created_at AT TIME ZONE 'America/New_York')::date AS date,
           count(*) AS total,
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = u.id AND p.type = 'player')) AS players,
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = u.id AND p.type = 'host')) AS hosts,
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = u.id AND p.type = 'venue')) AS venues
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

# Every sign-up, including ones that never finished (an auth.users row with no users row, no
# profile, or a profile missing its player/host/venue details). Links merge the profile_links
# rows and the player profile's game platforms across all of the account's profiles.
ACCOUNTS_SQL = text("""
    SELECT a.id, a.created_at, coalesce(u.email, a.email) AS email,
           coalesce(dp.username, (SELECT p.username FROM profiles p WHERE p.account_id = a.id ORDER BY p.created_at LIMIT 1)) AS username,
           EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = a.id AND p.type = 'player') AS is_player,
           EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = a.id AND p.type = 'host') AS is_host,
           EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = a.id AND p.type = 'venue') AS is_venue,
           (u.id IS NOT NULL
               AND EXISTS (SELECT 1 FROM profiles p WHERE p.account_id = a.id)
               AND NOT EXISTS (
                   SELECT 1 FROM profiles p
                   WHERE p.account_id = a.id AND CASE p.type
                       WHEN 'player' THEN NOT EXISTS (SELECT 1 FROM players x WHERE x.profile_id = p.id)
                       WHEN 'host' THEN NOT EXISTS (SELECT 1 FROM hosts x WHERE x.profile_id = p.id)
                       ELSE NOT EXISTS (SELECT 1 FROM venues x WHERE x.profile_id = p.id)
                   END
               )) AS profile_complete,
           ARRAY(
               SELECT DISTINCT g FROM profiles p
               LEFT JOIN players pl ON pl.profile_id = p.id
               LEFT JOIN hosts h ON h.profile_id = p.id
               CROSS JOIN LATERAL unnest(coalesce(pl.games, '{}') || coalesce(h.games, '{}')) AS g
               WHERE p.account_id = a.id
           ) AS games,
           coalesce((
               SELECT jsonb_object_agg(platform, handle) FROM (
                   SELECT DISTINCT ON (platform) platform, handle FROM (
                       SELECT l.platform::text AS platform, l.handle FROM profile_links l WHERE l.account_id = a.id
                       UNION ALL
                       SELECT v.platform, v.handle
                       FROM profiles p
                       JOIN players pl ON pl.profile_id = p.id
                       CROSS JOIN LATERAL (VALUES ('battlenet', pl.battlenet), ('activision', pl.activision),
                                                  ('steam', pl.steam), ('riot', pl.riot)) AS v(platform, handle)
                       WHERE p.account_id = a.id AND v.handle IS NOT NULL AND v.handle <> ''
                   ) all_links
                   ORDER BY platform
               ) one_per_platform
           ), '{}'::jsonb) AS linked
    FROM auth.users a
    LEFT JOIN users u ON u.id = a.id
    LEFT JOIN profiles dp ON dp.id = u.default_profile_id
    ORDER BY a.created_at DESC
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

async def get_admin_accounts(db: AsyncSession) -> dict:
    """
    Every account with its roles, profile status, games and linked platforms.

    ::param db the read-only database session

    ::return a dict matching AdminAccountsOut
    """

    accounts = [dict(row) for row in (await db.execute(ACCOUNTS_SQL)).mappings()]

    return {"generated_at": datetime.now(timezone.utc), "accounts": accounts}
