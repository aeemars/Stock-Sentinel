import secrets
from typing import Dict, Any, List, Tuple
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.config import settings

SESSION_COOKIE_NAME = "session"
MAX_AGE = 14 * 86400  # 14 days

def get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="stock-sentinel-session")

def load_session(cookie_val: str | None) -> Dict[str, Any]:
    if not cookie_val:
        return {}
    s = get_serializer()
    try:
        data = s.loads(cookie_val, max_age=MAX_AGE)
        if isinstance(data, dict):
            return data
    except (BadSignature, SignatureExpired):
        pass
    return {}

def dump_session(data: Dict[str, Any]) -> str:
    s = get_serializer()
    return s.dumps(data)

def flash(request: Request, message: str, category: str = "message") -> None:
    session = getattr(request.state, "session", None)
    if session is None:
        request.state.session = {}
        session = request.state.session
    flashes: List[Tuple[str, str]] = session.setdefault("_flashes", [])
    flashes.append((category, message))

def get_flashed_messages(request: Request, with_categories: bool = False) -> List[Any]:
    session = getattr(request.state, "session", None)
    if not session:
        return []
    flashes = session.pop("_flashes", [])
    if with_categories:
        return flashes
    return [msg for _, msg in flashes]

class StockSentinelSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        request.state.session = load_session(cookie)

        # Ensure CSRF token is present in session
        if "_csrf_token" not in request.state.session:
            request.state.session["_csrf_token"] = secrets.token_urlsafe(32)

        # Snapshot session state to detect changes
        initial_repr = str(sorted((k, str(v)) for k, v in request.state.session.items()))

        # Set request.endpoint convenience property for templates
        endpoint_func = request.scope.get("endpoint")
        name = endpoint_func.__name__ if endpoint_func and hasattr(endpoint_func, "__name__") else ""
        request.state.endpoint = name
        try:
            request.endpoint = name
        except Exception:
            pass

        response: Response = await call_next(request)

        # Performance: Only re-serialize and set cookie if session was mutated or is new
        session_data = getattr(request.state, "session", {})
        current_repr = str(sorted((k, str(v)) for k, v in session_data.items()))
        if current_repr != initial_repr or not cookie:
            token = dump_session(session_data)
            is_https = (
                request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto") == "https"
                or request.headers.get("x-forwarded-ssl") == "on"
                or "https" in request.headers.get("cf-visitor", "")
            )
            is_secure = (
                settings.SESSION_COOKIE_SECURE is True
                or (settings.SESSION_COOKIE_SECURE is None and (settings.is_serverless or is_https))
            )
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=token,
                max_age=MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=is_secure,
            )
        return response
