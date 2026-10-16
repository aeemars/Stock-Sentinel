from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user
from app.services.cache import cache
from app.templates_config import render_template

router = APIRouter(tags=["dashboard"])

@router.get("/", name="index", response_model=None)
def index(request: Request):
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@router.get("/dashboard", name="dashboard", response_model=None)
def dashboard(
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    # Tier 1 In-Memory Cache Lookup (<0.5ms)
    cache_key = f"data:user:{current_user.id}:dashboard"
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return render_template("dashboard.html", request, cached_payload)

    # Consolidated Single-Trip SQL Query with Aggregation (eliminates N+1 queries)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COALESCE(h_counts.cnt, 0) AS holdings_count
            FROM portfolios p
            LEFT JOIN (
                SELECT portfolio_id, COUNT(*) as cnt
                FROM holdings
                GROUP BY portfolio_id
            ) h_counts ON p.id = h_counts.portfolio_id
            WHERE p.user_id = %s
            ORDER BY p.id
        """, (current_user.id,))
        portfolios = cur.fetchall()

        total_holdings = 0
        for p in portfolios:
            cnt = p['holdings_count']
            p['holdings'] = [None] * cnt  # Preserves template {{ portfolio.holdings|length }}
            total_holdings += cnt

    payload = {"portfolios": portfolios, "total_holdings": total_holdings}
    cache.set(cache_key, payload, ttl=30.0)

    return render_template("dashboard.html", request, payload)
