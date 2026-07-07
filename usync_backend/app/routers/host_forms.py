from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.host_forms import HostFormIn, HostFormOut

router = APIRouter(prefix="/hostForms", tags=["Host Forms"])

@router.post("/hostform", response_model=HostFormOut)
async def submitHostForm(payload: HostFormIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to submit a host form.
    """

    # Need return function
