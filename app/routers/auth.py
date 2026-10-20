import secrets
from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from psycopg2.extras import RealDictCursor
from db import get_db
from app.config import settings
from app.session import SESSION_COOKIE_NAME, flash
from app.auth import User, hash_password, check_password, get_user_with_hash
from app.dependencies import get_current_user_optional, get_current_user, verify_csrf, is_safe_redirect
from app.services.cache import (
    set_cached_user,
    invalidate_user_cache,
    check_rate_limit,
    record_rate_limit,
    reset_rate_limit,
)
from app.templates_config import render_template

router = APIRouter(tags=["auth"])

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

@router.get("/register", name="register", response_model=None)
def register_page(request: Request, current_user=Depends(get_current_user_optional)):
    if current_user and current_user.is_authenticated:
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)
    return render_template("register.html", request)

@router.post("/register", name="register_post", dependencies=[Depends(verify_csrf)], response_model=None)
def register_action(
    request: Request,
    username: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    conn=Depends(get_db),
    current_user=Depends(get_current_user_optional)
):
    if current_user and current_user.is_authenticated:
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

    client_ip = get_client_ip(request)
    if not check_rate_limit(f"reg:{client_ip}", max_attempts=5):
        flash(request, "Too many registration attempts from this address. Please wait a few minutes.", "error")
        return render_template("register.html", request, {"username": username, "email": email})
    record_rate_limit(f"reg:{client_ip}", window_seconds=300)

    username = username.strip()
    email = email.strip().lower()

    # Validation
    errors = []
    if not username or len(username) < 3 or len(username) > 50:
        errors.append("Username must be between 3 and 50 characters.")
    if not email or "@" not in email or len(email) > 255:
        errors.append("A valid email address is required.")
    if not password or len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    elif len(password) > 72:
        errors.append("Password cannot exceed 72 characters.")
    if password != confirm_password:
        errors.append("Passwords do not match.")

    if errors:
        for err in errors:
            flash(request, err, "error")
        return render_template("register.html", request, {"username": username, "email": email})

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
            if cur.fetchone():
                flash(request, "Username or email already taken.", "error")
                return render_template("register.html", request, {"username": username, "email": email})

            pw_hash = hash_password(password)
            cur.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                (username, email, pw_hash)
            )
            new_user = cur.fetchone()
        conn.commit()

        # Session rotation upon registration (SEC-08)
        request.state.session.clear()
        request.state.session["user_id"] = new_user["id"]
        request.state.session["_csrf_token"] = secrets.token_urlsafe(32)
        flash(request, f"Welcome aboard, {username}!", "success")
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)
    except Exception as e:
        conn.rollback()
        flash(request, f"Registration error: {str(e)}", "error")
        return render_template("register.html", request, {"username": username, "email": email})

@router.get("/login", name="login", response_model=None)
def login_page(request: Request, current_user=Depends(get_current_user_optional)):
    if current_user and current_user.is_authenticated:
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)
    return render_template("login.html", request)

@router.post("/login", name="login_post", dependencies=[Depends(verify_csrf)], response_model=None)
def login_action(
    request: Request,
    identifier: str = Form(""),
    password: str = Form(""),
    next_url: Optional[str] = Query(None, alias="next"),
    conn=Depends(get_db),
    current_user=Depends(get_current_user_optional)
):
    if current_user and current_user.is_authenticated:
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

    identifier = identifier.strip()
    if not identifier or not password:
        flash(request, "Both username/email and password are required.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    # Rate limiting (SEC-03)
    client_ip = get_client_ip(request)
    rate_key = f"login:{client_ip}:{identifier.lower()}"
    if not check_rate_limit(rate_key, max_attempts=settings.MAX_LOGIN_ATTEMPTS):
        flash(request, f"Too many failed login attempts. Please wait {settings.LOGIN_LOCKOUT_SECONDS} seconds before trying again.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    result = get_user_with_hash(identifier, conn)
    if not result:
        record_rate_limit(rate_key, window_seconds=settings.LOGIN_LOCKOUT_SECONDS)
        flash(request, "Invalid username/email or password.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    user, pw_hash = result
    if not check_password(password, pw_hash):
        record_rate_limit(rate_key, window_seconds=settings.LOGIN_LOCKOUT_SECONDS)
        flash(request, "Invalid username/email or password.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    # Reset rate limit on successful authentication
    reset_rate_limit(rate_key)

    # Session rotation upon login to prevent session fixation (SEC-08)
    request.state.session.clear()
    request.state.session["user_id"] = user.id
    request.state.session["_csrf_token"] = secrets.token_urlsafe(32)
    request.state.user = user
    set_cached_user(user.id, user)
    flash(request, f"Welcome back, {user.username}!", "success")

    if next_url and is_safe_redirect(request, next_url):
        return RedirectResponse(url=next_url, status_code=303)
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@router.get("/logout", name="logout", response_model=None)
def logout(request: Request, current_user=Depends(get_current_user)):
    user_id = request.state.session.pop("user_id", None)
    if user_id:
        invalidate_user_cache(user_id)
    request.state.session.clear()
    flash(request, "You have been signed out.", "success")
    response = RedirectResponse(url=str(request.url_for("login")), status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
