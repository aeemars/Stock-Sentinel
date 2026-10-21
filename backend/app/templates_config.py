import secrets
from typing import Any, Dict, Optional
import jinja2
from starlette.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response
from app.session import get_flashed_messages
from app.auth import AnonymousUser

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR) if TEMPLATES_DIR.exists() else "templates")


@jinja2.pass_context
def jinja_csrf_token(context: jinja2.runtime.Context) -> str:
    request: Optional[Request] = context.get("request")
    if request and hasattr(request.state, "session"):
        token = request.state.session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            request.state.session["_csrf_token"] = token
        return token
    return ""

@jinja2.pass_context
def jinja_get_flashed_messages(context: jinja2.runtime.Context, with_categories: bool = False):
    request: Optional[Request] = context.get("request")
    if not request:
        return []
    return get_flashed_messages(request, with_categories=with_categories)

@jinja2.pass_context
def jinja_url_for(context: jinja2.runtime.Context, name: str, **path_params) -> str:
    request: Optional[Request] = context.get("request")
    if not request:
        return ""
    # Map Flask static url_for('static', filename='...') to Starlette path='...'
    if name == "static" and "filename" in path_params and "path" not in path_params:
        path_params["path"] = path_params.pop("filename")
    return str(request.url_for(name, **path_params))

templates.env.globals["csrf_token"] = jinja_csrf_token
templates.env.globals["get_flashed_messages"] = jinja_get_flashed_messages
templates.env.globals["url_for"] = jinja_url_for

def render_template(
    template_name: str,
    request: Request,
    context: Optional[Dict[str, Any]] = None,
    status_code: int = 200
) -> Response:
    ctx = {
        "request": request,
        "current_user": getattr(request.state, "user", AnonymousUser()),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
