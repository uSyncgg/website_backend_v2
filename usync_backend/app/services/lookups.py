from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.games import Games, GameTitles, GameModes, GameTitleModes
from app.models.brackets import BracketFormats

# The lookup tables (games, titles, modes, formats) as the event form and team form need them,
# plus resolving the keys a request sends into rows. These tables only change with a migration.

async def bracket_meta(db: AsyncSession) -> dict:
    """
    Everything the event form offers: games with their titles and modes (each title lists the
    modes it has), and the enabled formats.
    """

    games = (await db.execute(select(Games).order_by(Games.sort_order, Games.name))).scalars().all()
    titles = (await db.execute(select(GameTitles).order_by(GameTitles.release_year.desc().nulls_last(), GameTitles.name))).scalars().all()
    modes = (await db.execute(select(GameModes).order_by(GameModes.name))).scalars().all()
    title_modes = (await db.execute(select(GameTitleModes))).scalars().all()
    formats = (await db.execute(select(BracketFormats).where(BracketFormats.enabled.is_(True)).order_by(BracketFormats.sort_order))).scalars().all()

    mode_keys = {m.id: m.key for m in modes}
    by_title: dict = {}
    for tm in title_modes:
        by_title.setdefault(tm.title_id, []).append(mode_keys[tm.mode_id])

    return {
        "games": [
            {
                "key": g.key,
                "name": g.name,
                "titles": [
                    {"key": t.key, "name": t.name, "year": t.release_year, "modes": sorted(by_title.get(t.id, []))}
                    for t in titles if t.game_id == g.id
                ],
                "modes": [{"key": m.key, "name": m.name} for m in modes if m.game_id == g.id],
            }
            for g in games
        ],
        "formats": [
            {"key": f.key, "label": f.label, "description": f.description, "hasFinal": f.has_final, "minEntrants": f.min_entrants}
            for f in formats
        ],
    }

async def game_by_key(key: str, db: AsyncSession) -> Games:
    game = (await db.execute(select(Games).where(Games.key == key))).scalars().first()
    if not game:
        raise HTTPException(status_code=422, detail="Pick one of the supported games.")
    return game

async def games_by_keys(keys: list[str], db: AsyncSession) -> list[Games]:
    games = (await db.execute(select(Games).where(Games.key.in_(keys)))).scalars().all()
    if len(games) != len(set(keys)):
        raise HTTPException(status_code=422, detail="Pick from the supported games.")
    return list(games)

async def resolve_game(game_key: str, title_key: str | None, mode_key: str | None, db: AsyncSession) -> tuple:
    """
    (game, title | None, mode | None) for an event, checked against each other:
    - the title has to belong to the game, and a game with titles needs one picked;
    - the mode has to belong to the game, and to the title when there is one; once a game has
      modes, picking one is required.
    """

    game = await game_by_key(game_key, db)
    titles = (await db.execute(select(GameTitles).where(GameTitles.game_id == game.id))).scalars().all()
    title = None
    if titles:
        title = next((t for t in titles if t.key == title_key), None)
        if not title:
            raise HTTPException(status_code=422, detail=f"Pick which {game.name} game.")
    elif title_key:
        raise HTTPException(status_code=422, detail=f"{game.name} doesn't have titles to pick from.")

    modes = (await db.execute(select(GameModes).where(GameModes.game_id == game.id))).scalars().all()
    mode = None
    if modes:
        allowed = modes
        if title:
            title_mode_ids = set((await db.execute(select(GameTitleModes.mode_id).where(GameTitleModes.title_id == title.id))).scalars().all())
            allowed = [m for m in modes if m.id in title_mode_ids]
        mode = next((m for m in allowed if m.key == mode_key), None)
        if not mode:
            raise HTTPException(status_code=422, detail="Pick a game mode this game offers.")
    elif mode_key:
        raise HTTPException(status_code=422, detail=f"{game.name} doesn't have modes to pick from yet.")
    return game, title, mode

async def bracket_format(key: str, db: AsyncSession) -> BracketFormats:
    fmt = await db.get(BracketFormats, key)
    if not fmt or not fmt.enabled:
        raise HTTPException(status_code=422, detail="Pick one of the bracket formats offered.")
    return fmt
