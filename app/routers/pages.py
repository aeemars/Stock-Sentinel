from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app.dependencies import get_current_user, verify_csrf
from app.services.email import send_support_email
from app.services.market import dispatch_background_sync
from app.session import flash
from app.templates_config import render_template

router = APIRouter(tags=["pages"])

@router.get("/settings", name="settings", response_model=None)
def settings_page(request: Request, current_user=Depends(get_current_user)):
    return render_template("settings.html", request)

@router.get("/support", name="support", response_model=None)
def support_page(request: Request, current_user=Depends(get_current_user)):
    return render_template("support.html", request)

@router.post("/support/contact", name="support_contact", dependencies=[Depends(verify_csrf)])
async def support_contact(
    request: Request,
    subject: str = Form(""),
    category: str = Form("question"),
    message: str = Form(""),
    current_user=Depends(get_current_user),
):
    subject = subject.strip()
    category = category.strip().lower()
    message = message.strip()

    if not subject:
        if request.headers.get("accept") == "application/json":
            return JSONResponse({"status": "error", "detail": "Subject is required."}, status_code=422)
        flash(request, "Subject is required.", "error")
        return RedirectResponse(url=str(request.url_for("support")), status_code=303)

    if not message:
        if request.headers.get("accept") == "application/json":
            return JSONResponse({"status": "error", "detail": "Message is required."}, status_code=422)
        flash(request, "Message is required.", "error")
        return RedirectResponse(url=str(request.url_for("support")), status_code=303)

    # Dispatch email delivery asynchronously in a background daemon thread (<5ms HTTP response)
    dispatch_background_sync(
        send_support_email,
        current_user.username,
        current_user.email,
        category,
        subject,
        message,
    )

    success_msg = "Your message has been sent to the developer! We will get back to you shortly."
    flash(request, success_msg, "success")

    if request.headers.get("accept") == "application/json":
        return JSONResponse({"status": "success", "message": success_msg})

    return RedirectResponse(url=str(request.url_for("support")), status_code=303)
