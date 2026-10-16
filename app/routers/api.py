import secrets
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, status
from app.config import settings
from roma.workflow import run_root_workflow

router = APIRouter(prefix="/api", tags=["api"])

@router.post("/run-workflow", name="run_workflow_api")
def run_workflow_api(authorization: Optional[str] = Header(None)):
    cron_secret = settings.CRON_SECRET
    if not cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRON_SECRET is not configured"
        )

    expected = f"Bearer {cron_secret}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )

    try:
        run_root_workflow()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
