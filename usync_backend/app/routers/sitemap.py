from fastapi import APIRouter, Depends
from fastapi.responses import Response
from app.core.dependencies import get_sitemap_cache, get_db, verify_sitemap_token
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.sitemap import get_sitemap_xml, invalidate_sitemap_cache
from cachetools import TTLCache

router = APIRouter()

@router.get("/sitemap.xml")
async def get_sitemap(db: AsyncSession = Depends(get_db), cache: TTLCache = Depends(get_sitemap_cache)):
    """
    
    """

    xml = await get_sitemap_xml(db, cache)

    return Response(content=xml, media_type="application/xml")

@router.post("/sitemap/invalidate")
async def invalidate_sitemap(cache: TTLCache = Depends(get_sitemap_cache), _: None = Depends(verify_sitemap_token)):
    await invalidate_sitemap_cache(cache)

    return {"status": "invalidated"}
