from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
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
        print("\n" + "=" * 60)
        print("❌ DATABASE CONNECTION ERROR:")
        print(f"Could not connect to database: {settings.effective_database_url}")
        print("Please ensure your PostgreSQL database is running and configured in .env.")
        print("See README.md for Docker quickstart instructions:")
        print("  docker run --name stocks-postgres -e POSTGRES_USER=aeem -e POSTGRES_PASSWORD=bunymide -e POSTGRES_DB=stocks_db -p 5432:5432 -d postgres")
        print("=" * 60 + "\n")
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

# Mount static assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Progressive Web App (PWA) Core Routes
@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(
        "static/manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"}
    )

@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
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
    return RedirectResponse(url=str(request.url_for("login")), status_code=303)

@app.exception_handler(CSRFValidationException)
async def csrf_validation_handler(request: Request, exc: CSRFValidationException):
    return RedirectResponse(url=exc.redirect_url, status_code=303)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    # API endpoints receive standard 404; browser views redirect to dashboard
    if request.url.path.startswith("/api/"):
        return None
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return None
    flash(request, "An internal error occurred", "error")
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

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
