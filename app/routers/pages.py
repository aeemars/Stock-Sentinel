from fastapi import APIRouter, Depends, Request
from app.dependencies import get_current_user
from app.templates_config import render_template

router = APIRouter(tags=["pages"])

@router.get("/settings", name="settings", response_model=None)
def settings_page(request: Request, current_user=Depends(get_current_user)):
    return render_template("settings.html", request)

@router.get("/support", name="support", response_model=None)
def support_page(request: Request, current_user=Depends(get_current_user)):
    return render_template("support.html", request)
