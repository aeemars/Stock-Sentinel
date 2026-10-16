from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
import yfinance as yf
from db import get_db
from app.dependencies import get_current_user, verify_csrf
from app.session import flash
from app.templates_config import render_template

router = APIRouter(tags=["portfolios"])

@router.post("/portfolio/create", name="create_portfolio", dependencies=[Depends(verify_csrf)], response_model=None)
def create_portfolio(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    name = name.strip()
    description = description.strip()

    if not name:
        flash(request, "Portfolio name is required", "error")
        return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portfolios (name, description, user_id) VALUES (%s, %s, %s)",
                (name, description, current_user.id)
            )
        conn.commit()
        flash(request, f'Portfolio "{name}" created successfully!', "success")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error creating portfolio: {str(e)}", "error")

    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@router.get("/portfolio/{portfolio_id}", name="view_portfolio", response_model=None)
def view_portfolio(
    portfolio_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        portfolio = cur.fetchone()

        if not portfolio:
            flash(request, "Portfolio not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

        cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
        holdings = cur.fetchall()

        for holding in holdings:
            cur.execute("""
                SELECT * FROM holding_snapshots
                WHERE holding_id = %s
                ORDER BY created_at ASC
            """, (holding['id'],))
            holding['history'] = cur.fetchall()

        total_shares = sum(h['shares'] for h in holdings)
        unique_tickers = len(set(h['ticker'] for h in holdings))

        cur.execute("SELECT COUNT(*) as count FROM alerts WHERE portfolio_id = %s", (portfolio_id,))
        row = cur.fetchone()
        recent_alerts = row['count'] if row else 0

    return render_template(
        "portfolio.html",
        request,
        {
            "portfolio": portfolio,
            "holdings": holdings,
            "total_shares": total_shares,
            "unique_tickers": unique_tickers,
            "recent_alerts": recent_alerts,
        }
    )

@router.post("/portfolio/{portfolio_id}/delete", name="delete_portfolio", dependencies=[Depends(verify_csrf)], response_model=None)
def delete_portfolio(
    portfolio_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
            portfolio = cur.fetchone()

            if not portfolio:
                flash(request, "Portfolio not found", "error")
                return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

            portfolio_name = portfolio['name']

            cur.execute("DELETE FROM holding_snapshots WHERE portfolio_id = %s", (portfolio_id,))
            cur.execute("DELETE FROM alerts WHERE portfolio_id = %s", (portfolio_id,))
            cur.execute("DELETE FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
            cur.execute("DELETE FROM portfolios WHERE id = %s", (portfolio_id,))

        conn.commit()
        flash(request, f'Portfolio "{portfolio_name}" deleted successfully', "success")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error deleting portfolio: {str(e)}", "error")

    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@router.get("/portfolio/{portfolio_id}/edit", name="edit_portfolio", response_model=None)
def edit_portfolio(
    portfolio_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        if not cur.fetchone():
            flash(request, "Portfolio not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

    return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

@router.post("/portfolio/{portfolio_id}/refresh-prices", name="refresh_prices", dependencies=[Depends(verify_csrf)], response_model=None)
def refresh_prices(
    portfolio_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        if not cur.fetchone():
            flash(request, "Portfolio not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

        cur.execute("SELECT id, ticker FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
        rows = cur.fetchall()

    if not rows:
        flash(request, "No holdings to refresh", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    price_map = {}
    for row in rows:
        t = row['ticker']
        try:
            yf_price = yf.Ticker(t).fast_info.get('lastPrice')
            if not yf_price:
                df = yf.download(t, period='1d', progress=False, multi_level_index=False)
                if not df.empty:
                    yf_price = float(df['Close'].iloc[-1])
            if yf_price:
                price_map[t] = float(yf_price)
        except Exception:
            pass

    try:
        with conn.cursor() as cur:
            for row in rows:
                price = price_map.get(row['ticker'])
                if price is not None:
                    cur.execute(
                        """UPDATE holdings
                           SET last_price = %s, last_price_updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (price, row['id'])
                    )
        conn.commit()
        flash(request, "Prices refreshed successfully", "success")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error refreshing prices: {str(e)}", "error")

    return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)
