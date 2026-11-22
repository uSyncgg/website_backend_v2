"""Rulesets, and Call of Duty's game modes.

Revision ID: 9076146a6aa0
Revises: 4dd60a019a38
Create Date: 2026-11-22 00:00:00.000000

Written by hand. Adds the rulesets lookup table (GLOSSARY.md: Ruleset) and
bracket_events.ruleset_id, and seeds Call of Duty's modes and rulesets. RLS goes on for the new
table with no policies, like the other lookups: only the backend reads it.

A ruleset with game_title_id null is general (offered on every title of its game); one with a
title is offered only for that title. ruleset_id is nullable: games without rulesets have none,
and the API requires one once the event's game/title offers any (app.services.lookups).

Modes: Search and Destroy on every Call of Duty title, Hardpoint on Ghosts (2013) and later,
except Modern Warfare Remastered. Seeding them makes a Call of Duty event's mode required.
Seeds look rows up by key, never by id, since title and mode ids are serial.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9076146a6aa0'
down_revision: Union[str, Sequence[str], None] = '4dd60a019a38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HARDPOINT_TITLES = (
    'ghosts', 'advanced_warfare', 'black_ops_iii', 'infinite_warfare', 'wwii', 'black_ops_4',
    'call_of_duty_mobile', 'modern_warfare_2019', 'black_ops_cold_war', 'vanguard',
    'modern_warfare_ii', 'modern_warfare_iii', 'black_ops_6', 'black_ops_7', 'modern_warfare_4',
)

# (key, name, url, title key or None for general, sort_order)
RULESETS = (
    ('cdl', 'CDL Competitive Settings', 'https://callofdutyleague.com/en-us/competitive-settings', None, 1),
    ('codagent', 'CodAgent', 'https://esportsagent.gg/rules', None, 2),
    ('codagent_mw4', 'CodAgent – Modern Warfare 4', 'https://esportsagent.gg/rules/modern-warfare-4', 'modern_warfare_4', 3),
    ('checkmate_mw4', 'Checkmate Gaming – Modern Warfare 4', 'https://www.checkmategaming.com/ladder/cross-platform/call-of-duty-modern-warfare-4/rules', 'modern_warfare_4', 4),
    ('consolekings_mw4', 'ConsoleKings – Modern Warfare 4', 'https://www.consolekings.com/ladder/cross-platform/call-of-duty-mw4?tab=tournament&innerTab=rules', 'modern_warfare_4', 5),
)

COD = "(SELECT id FROM games WHERE key = 'call_of_duty')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rulesets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('game_id', sa.SmallInteger(), nullable=False),
        sa.Column('game_title_id', sa.Integer(), nullable=True),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['game_title_id'], ['game_titles.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('game_id', 'key', name='uq_rulesets_game_key'),
    )
    op.create_index('ix_rulesets_game_id', 'rulesets', ['game_id'])
    op.execute('ALTER TABLE rulesets ENABLE ROW LEVEL SECURITY')

    op.add_column('bracket_events', sa.Column('ruleset_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_bracket_events_ruleset_id', 'bracket_events', 'rulesets', ['ruleset_id'], ['id'], ondelete='RESTRICT')

    # ---- seed data -----------------------------------------------------------
    op.execute(f"""
        INSERT INTO game_modes (game_id, key, name) VALUES
            ({COD}, 'hardpoint', 'Hardpoint'),
            ({COD}, 'search_and_destroy', 'Search and Destroy')
    """)
    op.execute(f"""
        INSERT INTO game_title_modes (title_id, mode_id)
        SELECT t.id, m.id FROM game_titles t JOIN game_modes m ON m.game_id = t.game_id
        WHERE t.game_id = {COD} AND m.key = 'search_and_destroy'
    """)
    op.execute(
        sa.text(f"""
            INSERT INTO game_title_modes (title_id, mode_id)
            SELECT t.id, m.id FROM game_titles t JOIN game_modes m ON m.game_id = t.game_id
            WHERE t.game_id = {COD} AND m.key = 'hardpoint' AND t.key IN :titles
        """).bindparams(sa.bindparam('titles', value=HARDPOINT_TITLES, expanding=True))
    )
    bind = op.get_bind()
    for key, name, url, title_key, sort_order in RULESETS:
        # A title-specific ruleset is inserted only if its title exists, so a mistyped title key
        # inserts nothing (and fails the check below) instead of quietly becoming general.
        title_id = "NULL" if title_key is None else "t.id"
        title_join = "" if title_key is None else f"JOIN game_titles t ON t.game_id = g.id AND t.key = :title_key"
        bind.execute(
            sa.text(f"""
                INSERT INTO rulesets (game_id, game_title_id, key, name, url, sort_order)
                SELECT g.id, {title_id}, :key, :name, :url, :sort_order
                FROM games g {title_join}
                WHERE g.key = 'call_of_duty'
            """),
            {"key": key, "name": name, "url": url, "title_key": title_key, "sort_order": sort_order},
        )
    seeded = bind.execute(sa.text("SELECT count(*) FROM rulesets")).scalar()
    if seeded != len(RULESETS):
        raise RuntimeError(f"Seeded {seeded} of {len(RULESETS)} rulesets: check the game and title keys.")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_bracket_events_ruleset_id', 'bracket_events', type_='foreignkey')
    op.drop_column('bracket_events', 'ruleset_id')
    op.drop_index('ix_rulesets_game_id', table_name='rulesets')
    op.drop_table('rulesets')
    # Events that picked a Call of Duty mode keep pointing at it (RESTRICT), so clear them first.
    op.execute(f"UPDATE bracket_events SET game_mode_id = NULL WHERE game_mode_id IN (SELECT id FROM game_modes WHERE game_id = {COD})")
    op.execute(f"DELETE FROM game_modes WHERE game_id = {COD}")  # game_title_modes rows cascade
