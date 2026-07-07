from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.event_forms import FormReviewIn, FormReviewOut, FormSubmissionIn, FormSubmissionOut

router = APIRouter(prefix="/eventForm", tags=["Event Form"])

@router.post("/review", response_model=FormReviewOut)
def review_form_data(payload: FormReviewIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to review form data and return any issues with unique fields.
    """

    # Need to return function

@router.post("/submit", response_model=FormSubmissionOut)
def submit_form_data(payload: FormSubmissionIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to submit form data to the Supabase DB - If there is a duplicate record made at the exact moment of submission an error will be returned.
    """

    # Need to return function
