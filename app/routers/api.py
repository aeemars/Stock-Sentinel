import secrets
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request, status
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


@router.post("/webhooks/sendbyte", name="sendbyte_webhook")
async def sendbyte_webhook(
    request: Request,
    sendbyte_signature: Optional[str] = Header(None, alias="sendbyte-signature"),
    webhook_id: Optional[str] = Header(None, alias="webhook-id"),
):
    """
    Inbound webhook endpoint for SendByte email events (including 'email.received').
    Validates HMAC-SHA256 signature against raw request body if SENDBYTE_WEBHOOK_SECRET is set.
    """
    raw_body = await request.body()
    secret = settings.SENDBYTE_WEBHOOK_SECRET

    if secret:
        from app.services.email import verify_sendbyte_webhook_signature
        if not verify_sendbyte_webhook_signature(secret, sendbyte_signature, raw_body):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing webhook signature"
            )

    try:
        import json
        from app.services.email import handle_inbound_email_event
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        result = handle_inbound_email_event(payload)
        return {"status": "ok", "delivery_id": webhook_id, "result": result}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed webhook payload: {exc}"
        )
