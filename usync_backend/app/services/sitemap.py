from sqlalchemy.ext.asyncio import AsyncSession
from cachetools import TTLCache
from typing import Any
from sqlalchemy import select
from app.models.host_data import LanEvents, LeagueEvents, WagerEvents, XpEvents, LeagueParentEvents
from xml.etree.ElementTree import Element, SubElement, tostring

EVENT_TYPE_LOOKUP = {
    "leagues": LeagueEvents,
    "wagers": WagerEvents,
    "lans": LanEvents,
    "head-to-head": XpEvents,
    "league-parents": LeagueParentEvents
}

BASE_URL = "https://www.usync.gg"

def _format_sitemap_entries(entries: dict[str, list[dict[str, Any]]]) -> list[dict]:
    """
    
    """

    sitemap_entries = []

    for table in entries:
        for entry in entries[table]:
            temp_dict = {}

            if table != "lans":
                temp_dict["loc"] = f"{BASE_URL}/games/{entry['game']}{entry['path']}"
            else:
                temp_dict["loc"] = f"{BASE_URL}/lans{entry['path']}"

            temp_dict["lastmod"] = entry["updated_at"].isoformat()

            sitemap_entries.append(temp_dict)

    return sitemap_entries


async def _fetch_sitemap_entries(db: AsyncSession, event: str) -> list[dict]:
    """
    
    """

    table = EVENT_TYPE_LOOKUP[event]

    stmt = (
        select(table.path, table.updated_at, table.game)
        .where(table.status != "pending")
    )

    result = await db.execute(stmt)
    return result.mappings().all()


async def _to_sitemap_xml(db: AsyncSession) -> bytes:
    """
    
    """

    entries = {}

    for event in ["leagues", "wagers", "lans", "head-to-head", "league-parents"]:
        entries[event] = await _fetch_sitemap_entries(db, event)

    xml_list = _format_sitemap_entries(entries)

    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    for entry in xml_list:
        url_el = SubElement(urlset, "url")
        SubElement(url_el, "loc").text = entry["loc"]
        if lastmod := entry.get("lastmod"):
            SubElement(url_el, "lastmod").text = lastmod

    return tostring(urlset, encoding="utf-8", xml_declaration=True)


async def get_sitemap_xml(db: AsyncSession, cache: TTLCache) -> bytes:
    """
    
    """

    if xml := cache.get("sitemap"):
        return xml

    xml = await _to_sitemap_xml(db)
    cache["sitemap"] = xml
    return xml

async def invalidate_sitemap_cache(cache: TTLCache) -> None:
    """
    
    """

    cache.pop("sitemap", None)

    return None