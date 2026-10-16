import secrets
from urllib.parse import urlsplit
from typing import Optional
from fastapi import Depends, Request
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
    if not target:
        return False
    ref = urlsplit(str(request.base_url))
    test = urlsplit(target)
    return not test.netloc or (test.scheme in ('http', 'https') and test.netloc == ref.netloc)

from app.services.cache import get_cached_user, set_cached_user

def get_current_user_optional(request: Request) -> Optional[User]:
    user_id = request.state.session.get("user_id") if hasattr(request.state, "session") else None
    if user_id:
        # Tier 1 In-Memory Cache Check (<0.1ms)
        cached_user = get_cached_user(user_id)
        if cached_user:
            request.state.user = cached_user
            return cached_user

        from db import get_db_connection, put_db_connection
        try:
            conn = get_db_connection()
            try:
                user = get_user_by_id(user_id, conn)
                if user:
                    set_cached_user(user_id, user, ttl=60.0)
                    request.state.user = user
                    return user
            finally:
                put_db_connection(conn)
        except Exception:
            pass
    request.state.user = AnonymousUser()
    return None

def get_current_user(request: Request) -> User:
    user_id = request.state.session.get("user_id") if hasattr(request.state, "session") else None
    if not user_id:
        flash(request, "Please sign in to access this page.", "error")
        raise LoginRequiredException()

    user = get_current_user_optional(request)
    if not user or not user.is_authenticated:
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
            flash(request, "Your session expired. Please try again.", "error")
            ref = request.headers.get("referer")
            dest = ref if is_safe_redirect(request, ref) else str(request.url_for("dashboard"))
            raise CSRFValidationException(dest)
