from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import init_db
from scheduler import start_scheduler, stop_scheduler
from app.config import settings
from app.session import StockSentinelSessionMiddleware, flash
from app.dependencies import LoginRequiredException, CSRFValidationException
from app.templates_config import render_template

from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.portfolios import router as portfolios_router
from app.routers.holdings import router as holdings_router
from app.routers.alerts import router as alerts_router
from app.routers.analytics import router as analytics_router
from app.routers.pages import router as pages_router
from app.routers.api import router as api_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database schema & pool
    try:
        init_db(settings.effective_database_url)
    except Exception as exc:
        print(f"❌ DATABASE CONNECTION ERROR: {exc}")
        print(f"Effective Database URL: {settings.effective_database_url}")
        if not settings.is_serverless:
            raise exc


    # Start background scheduler if not in serverless environment (Vercel, Cloudflare, etc.)
    scheduler = None
    if not settings.is_serverless:
        try:
            scheduler = start_scheduler()
        except Exception as exc:
            print(f"Warning: Scheduler could not be started: {exc}")

    yield

    # Shutdown
    if scheduler:
        stop_scheduler()

app = FastAPI(
    title="Stock Sentinel",
    description="AI-powered stock monitoring dashboard and intelligence pipeline",
    lifespan=lifespan
)

import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

class PerformanceMiddleware(BaseHTTPMiddleware):
    """Adds X-Response-Time and Server-Timing headers, plus HTTP caching for static assets."""
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - t0) * 1000
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"

        # Static Asset Caching: 1-year immutable caching for static files
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return response

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces defensive HTTP response headers (SEC-07): Clickjacking, MIME-sniffing, and referrer policies."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:
    try:
        app.mount("/static", StaticFiles(directory="static"), name="static")
    except Exception:
        pass

# Progressive Web App (PWA) Core Routes
@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    manifest_file = STATIC_DIR / "manifest.webmanifest"
    target = str(manifest_file) if manifest_file.exists() else "static/manifest.webmanifest"
    return FileResponse(
        target,
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"}
    )

@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    sw_file = STATIC_DIR / "sw.js"
    target = str(sw_file) if sw_file.exists() else "static/sw.js"
    return FileResponse(
        target,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    fav_file = STATIC_DIR / "favicon.svg"
    target = str(fav_file) if fav_file.exists() else "static/favicon.svg"
    return FileResponse(
        target,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"}
    )

@app.get("/offline", name="offline", include_in_schema=False)
async def pwa_offline(request: Request):
    return render_template("offline.html", request)

# Middlewares (Security & Performance outermost, then Compression & Session)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PerformanceMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(StockSentinelSessionMiddleware)

# Exception handlers matching Flask application behavior
@app.exception_handler(LoginRequiredException)
async def login_required_handler(request: Request, exc: LoginRequiredException):
    return RedirectResponse(url="/login", status_code=303)

@app.exception_handler(CSRFValidationException)
async def csrf_validation_handler(request: Request, exc: CSRFValidationException):
    return RedirectResponse(url=exc.redirect_url, status_code=303)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    # API endpoints receive standard 404 JSON; browser views redirect to dashboard
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not Found")
        return JSONResponse(status_code=404, content={"detail": detail})
    return RedirectResponse(url="/dashboard", status_code=303)

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    flash(request, "An internal error occurred", "error")
    return RedirectResponse(url="/dashboard", status_code=303)

# Include route modules
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(portfolios_router)
app.include_router(holdings_router)
app.include_router(alerts_router)
app.include_router(analytics_router)
app.include_router(pages_router)
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=5000, reload=True)
