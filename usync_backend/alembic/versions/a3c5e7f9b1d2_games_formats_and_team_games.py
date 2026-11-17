"""Lookup tables for games, titles, modes and bracket formats, and the games a team plays.

Revision ID: a3c5e7f9b1d2
Revises: f8d2b6c41a07
Create Date: 2026-11-14 12:00:00.000000

Written by hand. Adds the lookup tables events and teams will reference instead of free-text
game names, seeds them, and turns RLS on for every new table (no policies: only the backend,
which bypasses RLS, reads or writes them).

Titles are optional: a game with no titles simply has no game_titles rows, and an event for it
leaves game_title_id null. Modes belong to the game (game_modes.game_id); game_title_modes
narrows them for games that do have titles. Every game gets at least one mode in the migration
that seeds modes, which also makes an event's mode required.

The old text columns (bracket_events.game / game_title, teams.game) stay for now and are
dropped once the services read the new columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, Sequence[str], None] = 'f8d2b6c41a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ('games', 'game_titles', 'game_modes', 'game_title_modes', 'bracket_formats', 'team_games')


def upgrade() -> None:
    """Upgrade schema."""
    # ---- lookup tables -------------------------------------------------------
    games = op.create_table(
        'games',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    titles = op.create_table(
        'game_titles',
        sa.Column('id', sa.Integer(), sa.Identity(), nullable=False),
        sa.Column('game_id', sa.SmallInteger(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('release_year', sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('game_id', 'key', name='uq_game_titles_game_key'),
    )
    op.create_index('ix_game_titles_game_id', 'game_titles', ['game_id'])
    op.create_table(
        'game_modes',
        sa.Column('id', sa.Integer(), sa.Identity(), nullable=False),
        sa.Column('game_id', sa.SmallInteger(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('game_id', 'key', name='uq_game_modes_game_key'),
    )
    op.create_index('ix_game_modes_game_id', 'game_modes', ['game_id'])
    op.create_table(
        'game_title_modes',
        sa.Column('title_id', sa.Integer(), nullable=False),
        sa.Column('mode_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['title_id'], ['game_titles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['mode_id'], ['game_modes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('title_id', 'mode_id'),
    )
    op.create_index('ix_game_title_modes_mode_id', 'game_title_modes', ['mode_id'])
    formats = op.create_table(
        'bracket_formats',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('has_final', sa.Boolean(), nullable=False),
        sa.Column('min_entrants', sa.SmallInteger(), server_default='2', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )

    # ---- seed data -----------------------------------------------------------
    # Games: the same names the site already uses (app.services.GAMES).
    op.bulk_insert(games, [
        {'id': 1, 'key': 'call_of_duty', 'name': 'Call of Duty', 'sort_order': 1},
        {'id': 2, 'key': 'warzone', 'name': 'Warzone', 'sort_order': 2},
        {'id': 3, 'key': 'halo', 'name': 'Halo', 'sort_order': 3},
        {'id': 4, 'key': 'cs2', 'name': 'CS2', 'sort_order': 4},
        {'id': 5, 'key': 'league_of_legends', 'name': 'League of Legends', 'sort_order': 5},
        {'id': 6, 'key': 'rocket_league', 'name': 'Rocket League', 'sort_order': 6},
        {'id': 7, 'key': 'valorant', 'name': 'Valorant', 'sort_order': 7},
    ])
    # Titles: the lists the event form already offers. Other games have no titles.
    op.bulk_insert(titles, [
        {'game_id': 1, 'key': 'modern_warfare_4', 'name': 'Modern Warfare 4', 'release_year': 2026},
        {'game_id': 1, 'key': 'black_ops_7', 'name': 'Black Ops 7', 'release_year': 2025},
        {'game_id': 1, 'key': 'black_ops_6', 'name': 'Black Ops 6', 'release_year': 2024},
        {'game_id': 1, 'key': 'modern_warfare_iii', 'name': 'Modern Warfare III', 'release_year': 2023},
        {'game_id': 1, 'key': 'modern_warfare_ii', 'name': 'Modern Warfare II', 'release_year': 2022},
        {'game_id': 1, 'key': 'vanguard', 'name': 'Vanguard', 'release_year': 2021},
        {'game_id': 1, 'key': 'black_ops_cold_war', 'name': 'Black Ops Cold War', 'release_year': 2020},
        {'game_id': 1, 'key': 'modern_warfare_2019', 'name': 'Modern Warfare (2019)', 'release_year': 2019},
        {'game_id': 1, 'key': 'call_of_duty_mobile', 'name': 'Call of Duty: Mobile', 'release_year': 2019},
        {'game_id': 1, 'key': 'black_ops_4', 'name': 'Black Ops 4', 'release_year': 2018},
        {'game_id': 1, 'key': 'wwii', 'name': 'WWII', 'release_year': 2017},
        {'game_id': 1, 'key': 'infinite_warfare', 'name': 'Infinite Warfare', 'release_year': 2016},
        {'game_id': 1, 'key': 'modern_warfare_remastered', 'name': 'Modern Warfare Remastered', 'release_year': 2016},
        {'game_id': 1, 'key': 'black_ops_iii', 'name': 'Black Ops III', 'release_year': 2015},
        {'game_id': 1, 'key': 'advanced_warfare', 'name': 'Advanced Warfare', 'release_year': 2014},
        {'game_id': 1, 'key': 'ghosts', 'name': 'Ghosts', 'release_year': 2013},
        {'game_id': 1, 'key': 'black_ops_ii', 'name': 'Black Ops II', 'release_year': 2012},
        {'game_id': 1, 'key': 'modern_warfare_3', 'name': 'Modern Warfare 3', 'release_year': 2011},
        {'game_id': 1, 'key': 'black_ops', 'name': 'Black Ops', 'release_year': 2010},
        {'game_id': 1, 'key': 'modern_warfare_2', 'name': 'Modern Warfare 2', 'release_year': 2009},
        {'game_id': 1, 'key': 'world_at_war', 'name': 'World at War', 'release_year': 2008},
        {'game_id': 1, 'key': 'call_of_duty_4_modern_warfare', 'name': 'Call of Duty 4: Modern Warfare', 'release_year': 2007},
        {'game_id': 1, 'key': 'call_of_duty_3', 'name': 'Call of Duty 3', 'release_year': 2006},
        {'game_id': 1, 'key': 'call_of_duty_2', 'name': 'Call of Duty 2', 'release_year': 2005},
        {'game_id': 1, 'key': 'call_of_duty_finest_hour', 'name': 'Call of Duty: Finest Hour', 'release_year': 2004},
        {'game_id': 1, 'key': 'call_of_duty_united_offensive', 'name': 'Call of Duty: United Offensive', 'release_year': 2004},
        {'game_id': 1, 'key': 'call_of_duty', 'name': 'Call of Duty', 'release_year': 2003},
        {'game_id': 3, 'key': 'halo_campaign_evolved', 'name': 'Halo: Campaign Evolved', 'release_year': 2026},
        {'game_id': 3, 'key': 'halo_infinite', 'name': 'Halo Infinite', 'release_year': 2021},
        {'game_id': 3, 'key': 'halo_wars_2', 'name': 'Halo Wars 2', 'release_year': 2017},
        {'game_id': 3, 'key': 'halo_5_guardians', 'name': 'Halo 5: Guardians', 'release_year': 2015},
        {'game_id': 3, 'key': 'halo_the_master_chief_collection', 'name': 'Halo: The Master Chief Collection', 'release_year': 2014},
        {'game_id': 3, 'key': 'halo_4', 'name': 'Halo 4', 'release_year': 2012},
        {'game_id': 3, 'key': 'halo_combat_evolved_anniversary', 'name': 'Halo: Combat Evolved Anniversary', 'release_year': 2011},
        {'game_id': 3, 'key': 'halo_reach', 'name': 'Halo: Reach', 'release_year': 2010},
        {'game_id': 3, 'key': 'halo_3_odst', 'name': 'Halo 3: ODST', 'release_year': 2009},
        {'game_id': 3, 'key': 'halo_wars', 'name': 'Halo Wars', 'release_year': 2009},
        {'game_id': 3, 'key': 'halo_3', 'name': 'Halo 3', 'release_year': 2007},
        {'game_id': 3, 'key': 'halo_2', 'name': 'Halo 2', 'release_year': 2004},
        {'game_id': 3, 'key': 'halo_combat_evolved', 'name': 'Halo: Combat Evolved', 'release_year': 2001},
    ])
    # Modes are seeded in a later migration once the lists are agreed.
    op.bulk_insert(formats, [
        {'key': 'single_elim', 'label': 'Single Elimination', 'description': "Lose once and you're out. Fast, classic bracket.", 'has_final': True, 'min_entrants': 2, 'sort_order': 1},
        {'key': 'double_elim', 'label': 'Double Elimination', 'description': 'Winners and losers brackets - lose twice to be out.', 'has_final': True, 'min_entrants': 2, 'sort_order': 2},
        {'key': 'round_robin', 'label': 'Round Robin', 'description': 'Everyone plays everyone once. Best for small groups and leagues.', 'has_final': False, 'min_entrants': 2, 'sort_order': 3},
        {'key': 'swiss', 'label': 'Swiss', 'description': 'A set number of rounds; each round pairs teams with the same record.', 'has_final': False, 'min_entrants': 2, 'sort_order': 4},
    ])

    # ---- events point at the lookups ------------------------------------------
    op.add_column('bracket_events', sa.Column('game_id', sa.SmallInteger(), nullable=True))
    op.add_column('bracket_events', sa.Column('game_title_id', sa.Integer(), nullable=True))
    op.add_column('bracket_events', sa.Column('game_mode_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_bracket_events_game_id', 'bracket_events', 'games', ['game_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_bracket_events_game_title_id', 'bracket_events', 'game_titles', ['game_title_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_bracket_events_game_mode_id', 'bracket_events', 'game_modes', ['game_mode_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_bracket_events_game_id', 'bracket_events', ['game_id'])
    # The format is now a foreign key to bracket_formats rather than a hard-coded list.
    op.drop_constraint('ck_bracket_events_format', 'bracket_events', type_='check')
    op.create_foreign_key('fk_bracket_events_format', 'bracket_events', 'bracket_formats', ['format'], ['key'], ondelete='RESTRICT')
    # Link events that already exist (only test events so far) to their game by name.
    op.execute("""
        UPDATE bracket_events e SET game_id = g.id FROM games g
        WHERE lower(replace(e.game, ' ', '')) = lower(replace(g.name, ' ', ''))
    """)

    # ---- the games a team plays -----------------------------------------------
    op.create_table(
        'team_games',
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('game_id', sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('team_id', 'game_id'),
    )
    op.create_index('ix_team_games_game_id', 'team_games', ['game_id'])

    for table in NEW_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_team_games_game_id', table_name='team_games')
    op.drop_table('team_games')
    op.drop_constraint('fk_bracket_events_format', 'bracket_events', type_='foreignkey')
    op.create_check_constraint('ck_bracket_events_format', 'bracket_events', "format IN ('single_elim', 'double_elim', 'round_robin', 'swiss')")
    op.drop_index('ix_bracket_events_game_id', table_name='bracket_events')
    op.drop_constraint('fk_bracket_events_game_mode_id', 'bracket_events', type_='foreignkey')
    op.drop_constraint('fk_bracket_events_game_title_id', 'bracket_events', type_='foreignkey')
    op.drop_constraint('fk_bracket_events_game_id', 'bracket_events', type_='foreignkey')
    op.drop_column('bracket_events', 'game_mode_id')
    op.drop_column('bracket_events', 'game_title_id')
    op.drop_column('bracket_events', 'game_id')
    op.drop_table('bracket_formats')
    op.drop_index('ix_game_title_modes_mode_id', table_name='game_title_modes')
    op.drop_table('game_title_modes')
    op.drop_index('ix_game_modes_game_id', table_name='game_modes')
    op.drop_table('game_modes')
    op.drop_index('ix_game_titles_game_id', table_name='game_titles')
    op.drop_table('game_titles')
    op.drop_table('games')
