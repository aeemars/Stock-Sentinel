from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user
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
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM portfolios WHERE user_id = %s ORDER BY id", (current_user.id,))
        portfolios = cur.fetchall()

        for portfolio in portfolios:
            cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s", (portfolio['id'],))
            portfolio['holdings'] = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) as count FROM holdings h
            JOIN portfolios p ON h.portfolio_id = p.id
            WHERE p.user_id = %s
        """, (current_user.id,))
        row = cur.fetchone()
        total_holdings = row['count'] if row else 0

    return render_template(
        "dashboard.html",
        request,
        {"portfolios": portfolios, "total_holdings": total_holdings}
    )
