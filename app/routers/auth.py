from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from psycopg2.extras import RealDictCursor
from db import get_db
from app.session import flash
from app.auth import User, hash_password, check_password, get_user_with_hash
from app.dependencies import get_current_user_optional, get_current_user, verify_csrf, is_safe_redirect
from app.services.cache import set_cached_user, invalidate_user_cache
from app.templates_config import render_template

router = APIRouter(tags=["auth"])

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

    username = username.strip()
    email = email.strip().lower()

    # Validation
    errors = []
    if not username or len(username) < 3:
        errors.append("Username must be at least 3 characters.")
    if not email or "@" not in email:
        errors.append("A valid email is required.")
    if not password or len(password) < 6:
        errors.append("Password must be at least 6 characters.")
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

        # Auto-login after registration
        request.state.session["user_id"] = new_user["id"]
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

    result = get_user_with_hash(identifier, conn)
    if not result:
        flash(request, "Invalid username/email or password.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    user, pw_hash = result
    if not check_password(password, pw_hash):
        flash(request, "Invalid username/email or password.", "error")
        return render_template("login.html", request, {"identifier": identifier})

    request.state.session["user_id"] = user.id
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
    flash(request, "You have been signed out.", "success")
    return RedirectResponse(url=str(request.url_for("login")), status_code=303)
