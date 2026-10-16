from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user, verify_csrf
from app.services.market import sync_portfolio_prices, calculate_holding_metrics
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

    # Dynamically pull latest market prices from yfinance if stale (>60s) or missing
    try:
        sync_portfolio_prices(portfolio_id, conn, max_age_seconds=60)
    except Exception:
        pass

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s ORDER BY ticker ASC", (portfolio_id,))
        holdings = cur.fetchall()

        total_cost = 0.0
        total_market_value = 0.0
        last_updated_at = None

        for holding in holdings:
            cur.execute("""
                SELECT * FROM holding_snapshots
                WHERE holding_id = %s
                ORDER BY created_at ASC
            """, (holding['id'],))
            holding['history'] = cur.fetchall()

            metrics = calculate_holding_metrics(holding)
            holding.update(metrics)

            total_cost += metrics['cost_basis']
            total_market_value += metrics['market_value']

            ts = holding.get('last_price_updated_at')
            if ts and (last_updated_at is None or ts > last_updated_at):
                last_updated_at = ts

        total_shares = sum(h['shares'] for h in holdings)
        unique_tickers = len(set(h['ticker'] for h in holdings))

        total_pnl = round(total_market_value - total_cost, 2) if total_cost > 0 else 0.0
        total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost > 0 else 0.0

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
            "total_cost": total_cost,
            "total_market_value": total_market_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "last_updated_at": last_updated_at,
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

    try:
        updated = sync_portfolio_prices(portfolio_id, conn, force=True)
        if updated:
            flash(request, f"Updated market prices for {len(updated)} stocks", "success")
        else:
            flash(request, "Prices refreshed successfully", "success")
    except Exception as e:
        flash(request, f"Error refreshing prices: {str(e)}", "error")

    return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)


@router.get("/portfolio/{portfolio_id}/live-prices", name="get_portfolio_live_prices")
def get_portfolio_live_prices(
    portfolio_id: int,
    request: Request,
    force: bool = False,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Portfolio not found")

    # Sync any prices older than 15 seconds (or forced)
    try:
        sync_portfolio_prices(portfolio_id, conn, max_age_seconds=15, force=force)
    except Exception:
        pass

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s ORDER BY ticker ASC", (portfolio_id,))
        holdings = cur.fetchall()

    results = []
    total_cost = 0.0
    total_market_value = 0.0
    latest_update = None

    for h in holdings:
        metrics = calculate_holding_metrics(h)
        total_cost += metrics['cost_basis']
        total_market_value += metrics['market_value']

        ts = h.get('last_price_updated_at')
        if ts and (latest_update is None or ts > latest_update):
            latest_update = ts

        results.append({
            "id": h['id'],
            "ticker": h['ticker'],
            "shares": h['shares'],
            "buy_price": round(h['buy_price'], 2) if h.get('buy_price') is not None else None,
            "last_price": round(h['last_price'], 2) if h.get('last_price') is not None else None,
            "cost_basis": metrics['cost_basis'],
            "market_value": metrics['market_value'],
            "pnl": metrics['pnl'],
            "pnl_pct": metrics['pnl_pct'],
            "updated_at": ts.strftime('%H:%M:%S') if ts else None
        })

    total_shares = sum(h['shares'] for h in holdings)
    total_pnl = round(total_market_value - total_cost, 2) if total_cost > 0 else 0.0
    total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost > 0 else 0.0

    return {
        "status": "success",
        "last_updated": latest_update.strftime('%H:%M:%S') if latest_update else "Live",
        "summary": {
            "total_holdings": len(results),
            "total_shares": round(total_shares, 2),
            "total_cost": total_cost,
            "total_market_value": total_market_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct
        },
        "holdings": results
    }

