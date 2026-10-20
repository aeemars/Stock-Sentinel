import secrets
from urllib.parse import urlsplit
from typing import Optional
from fastapi import Depends, HTTPException, Request
from db import get_db
from app.auth import User, AnonymousUser, get_user_by_id
from app.session import flash

class LoginRequiredException(Exception):
    """Raised when an unauthenticated request attempts to access a protected page."""
    pass

class CSRFValidationException(Exception):
    """Raised when a CSRF token check fails."""
    def __init__(self, redirect_url: str):
        self.redirect_url = redirect_url
        super().__init__(f"CSRF verification failed; redirect to {redirect_url}")

def is_safe_redirect(request: Request, target: Optional[str]) -> bool:
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target:
        return False

    test = urlsplit(target)

    # If scheme is present, it MUST be http or https, and netloc MUST match current host
    if test.scheme:
        if test.scheme not in ('http', 'https'):
            return False
        ref = urlsplit(str(request.base_url))
        return test.netloc == ref.netloc

    # Relative paths: must start with exactly one '/', not '//', '/\', or '\'
    if target.startswith('/') and not target.startswith(('//', '/\\', '\\')):
        return not test.netloc
    return False

def is_api_request(request: Request) -> bool:
    """Detect if an incoming request explicitly expects a JSON API response rather than HTML."""
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.url.path.startswith("/api/")

from app.services.cache import get_cached_user, set_cached_user

def get_current_user_optional(request: Request) -> Optional[User]:
    if hasattr(request.state, "user"):
        return request.state.user

    user_id = request.state.session.get("user_id") if hasattr(request.state, "session") else None
    if user_id:
        # Tier 1 In-Memory Cache Check (<0.1ms)
        cached_user = get_cached_user(user_id)
        if cached_user:
            request.state.user = cached_user
            return cached_user

        from db import get_db_connection, put_db_connection
        user = None
        db_error = False

        # Attempt up to 2 times to handle transient pool reconnections
        for _ in range(2):
            try:
                conn = get_db_connection()
                try:
                    user = get_user_by_id(user_id, conn)
                    db_error = False
                    break
                finally:
                    put_db_connection(conn)
            except Exception:
                db_error = True

        if user:
            set_cached_user(user_id, user, ttl=900.0)  # 15 minutes TTL
            request.state.user = user
            return user

        if not db_error and user is None:
            # Query succeeded and user was truly deleted/invalid in DB
            request.state.user = AnonymousUser()
            return None

    request.state.user = AnonymousUser()
    return None

def get_current_user(request: Request) -> User:
    user_id = request.state.session.get("user_id") if hasattr(request.state, "session") else None
    if not user_id:
        if is_api_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized")
        flash(request, "Please sign in to access this page.", "error")
        raise LoginRequiredException()

    user = get_current_user_optional(request)
    if not user or not user.is_authenticated:
        if is_api_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized")
        flash(request, "Please sign in to access this page.", "error")
        raise LoginRequiredException()
    return user

async def verify_csrf(request: Request) -> None:
    # Only runs for POST form submissions
    if request.method == "POST":
        form = await request.form()
        submitted = form.get("csrf_token", "")
        expected = request.state.session.get("_csrf_token", "")
        if not expected or not secrets.compare_digest(expected, str(submitted)):
            if is_api_request(request):
                raise HTTPException(status_code=403, detail="CSRF verification failed")
            flash(request, "Your session expired. Please try again.", "error")
            ref = request.headers.get("referer")
            dest = ref if is_safe_redirect(request, ref) else str(request.url_for("dashboard"))
            raise CSRFValidationException(dest)
