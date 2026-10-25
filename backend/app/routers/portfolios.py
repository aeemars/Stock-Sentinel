from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user, verify_csrf
from app.services.cache import cache
from app.services.market import (
    sync_portfolio_prices,
    background_sync_portfolio_prices,
    calculate_holding_metrics,
    dispatch_background_sync,
    is_ngx_ticker,
    get_ticker_currency,
)
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
        # Invalidate user data cache
        cache.delete_by_prefix(f"data:user:{current_user.id}:")
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
    cache_key = f"data:portfolio_view:{current_user.id}:{portfolio_id}"
    cached_context = cache.get(cache_key)
    if cached_context is not None:
        return render_template("portfolio.html", request, cached_context)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Verify ownership (indexed query)
        cur.execute("SELECT * FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        portfolio = cur.fetchone()

        if not portfolio:
            flash(request, "Portfolio not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

        # 2. Fetch holdings (indexed query)
        cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s ORDER BY ticker ASC", (portfolio_id,))
        holdings = cur.fetchall()

        # 3. Batch fetch all snapshots for all holdings in this portfolio (eliminates N+1 loop)
        cur.execute("""
            SELECT * FROM holding_snapshots
            WHERE portfolio_id = %s
            ORDER BY holding_id, created_at ASC
        """, (portfolio_id,))
        all_snapshots = cur.fetchall()

        snapshots_by_holding = {}
        for snap in all_snapshots:
            hid = snap['holding_id']
            if hid not in snapshots_by_holding:
                snapshots_by_holding[hid] = []
            snapshots_by_holding[hid].append(snap)

        total_cost = 0.0
        total_market_value = 0.0
        last_updated_at = None
        needs_price_sync = False
        now_utc = datetime.now(timezone.utc)

        totals_by_currency = {
            "USD": {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": "$"},
            "NGN": {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": "₦"},
        }

        for holding in holdings:
            holding['history'] = snapshots_by_holding.get(holding['id'], [])
            curr = holding.get('currency') or get_ticker_currency(holding['ticker'])
            holding['currency'] = curr

            metrics = calculate_holding_metrics(holding)
            holding.update(metrics)

            total_cost += metrics['cost_basis']
            total_market_value += metrics['market_value']

            if curr not in totals_by_currency:
                totals_by_currency[curr] = {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": curr}
            totals_by_currency[curr]["cost"] += metrics["cost_basis"]
            totals_by_currency[curr]["market_value"] += metrics["market_value"]

            ts = holding.get('last_price_updated_at')
            if ts:
                if last_updated_at is None or ts > last_updated_at:
                    last_updated_at = ts
                # Check freshness (>60s)
                ts_cmp = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                if (now_utc - ts_cmp).total_seconds() > 60:
                    needs_price_sync = True
            else:
                needs_price_sync = True

        for c, tdata in totals_by_currency.items():
            t_c = tdata["cost"]
            t_m = tdata["market_value"]
            t_pnl = round(t_m - t_c, 2) if t_c > 0 else 0.0
            t_pnl_pct = round((t_pnl / t_c) * 100, 2) if t_c > 0 else 0.0
            tdata["pnl"] = t_pnl
            tdata["pnl_pct"] = t_pnl_pct

        active_currencies = [c for c, d in totals_by_currency.items() if d["cost"] > 0 or d["market_value"] > 0]
        if not active_currencies and holdings:
            active_currencies = list({h.get("currency", "USD") for h in holdings})
        is_mixed_currency = len(active_currencies) > 1
        primary_currency = active_currencies[0] if len(active_currencies) == 1 else "USD"

        total_shares = sum(h['shares'] for h in holdings)
        unique_tickers = len(set(h['ticker'] for h in holdings))

        total_pnl = round(total_market_value - total_cost, 2) if total_cost > 0 else 0.0
        total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost > 0 else 0.0

        cur.execute("SELECT COUNT(*) as count FROM alerts WHERE portfolio_id = %s", (portfolio_id,))
        row = cur.fetchone()
        recent_alerts = row['count'] if row else 0

        cur.execute("""
            SELECT id, message, created_at
            FROM alerts
            WHERE portfolio_id = %s
            ORDER BY created_at DESC
            LIMIT 5
        """, (portfolio_id,))
        portfolio_alerts = cur.fetchall()

    # Non-blocking detached background sync: runs in daemon thread without blocking HTTP response (<20ms)
    if needs_price_sync and holdings:
        dispatch_background_sync(background_sync_portfolio_prices, portfolio_id, 60)

    context = {
        "portfolio": portfolio,
        "holdings": holdings,
        "total_shares": total_shares,
        "unique_tickers": unique_tickers,
        "recent_alerts": recent_alerts,
        "portfolio_alerts": portfolio_alerts,
        "total_cost": total_cost,
        "total_market_value": total_market_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "totals_by_currency": totals_by_currency,
        "active_currencies": active_currencies,
        "is_mixed_currency": is_mixed_currency,
        "primary_currency": primary_currency,
        "last_updated_at": last_updated_at,
    }
    # Cache view context for 20 seconds
    cache.set(cache_key, context, ttl=20)

    return render_template(
        "portfolio.html",
        request,
        context
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
        # Invalidate user data cache and portfolio live prices cache
        cache.delete_by_prefix(f"data:user:{current_user.id}:")
        cache.delete(f"data:live_prices:{portfolio_id}")
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
        cache.delete_by_prefix(f"data:user:{current_user.id}:")
        cache.delete(f"data:live_prices:{portfolio_id}")
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
    cache_key = f"data:live_prices:{portfolio_id}"

    # Tier 1 In-Memory Cache: Return within <1ms if cached and not forced
    if not force:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Portfolio not found")

    # If force refresh requested, sync synchronously and invalidate user cache
    if force:
        try:
            sync_portfolio_prices(portfolio_id, conn, force=True)
            cache.delete_by_prefix(f"data:user:{current_user.id}:")
        except Exception:
            pass

    # Read latest holdings from DB (indexed query, <3ms)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM holdings WHERE portfolio_id = %s ORDER BY ticker ASC", (portfolio_id,))
        holdings = cur.fetchall()

    results = []
    total_cost = 0.0
    total_market_value = 0.0
    latest_update = None
    needs_sync = False
    now_utc = datetime.now(timezone.utc)

    totals_by_currency = {
        "USD": {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": "$"},
        "NGN": {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": "₦"},
    }

    for h in holdings:
        curr = h.get('currency') or get_ticker_currency(h['ticker'])
        metrics = calculate_holding_metrics(h)
        total_cost += metrics['cost_basis']
        total_market_value += metrics['market_value']

        if curr not in totals_by_currency:
            totals_by_currency[curr] = {"cost": 0.0, "market_value": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "symbol": curr}
        totals_by_currency[curr]["cost"] += metrics["cost_basis"]
        totals_by_currency[curr]["market_value"] += metrics["market_value"]

        ts = h.get('last_price_updated_at')
        if ts:
            if latest_update is None or ts > latest_update:
                latest_update = ts
            ts_cmp = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if (now_utc - ts_cmp).total_seconds() > 30:
                needs_sync = True
        else:
            needs_sync = True

        results.append({
            "id": h['id'],
            "ticker": h['ticker'],
            "currency": curr,
            "currency_symbol": "₦" if curr == "NGN" else "$",
            "shares": h['shares'],
            "buy_price": round(h['buy_price'], 2) if h.get('buy_price') is not None else None,
            "last_price": round(h['last_price'], 2) if h.get('last_price') is not None else None,
            "cost_basis": metrics['cost_basis'],
            "market_value": metrics['market_value'],
            "pnl": metrics['pnl'],
            "pnl_pct": metrics['pnl_pct'],
            "updated_at": ts.strftime('%H:%M:%S') if ts else None
        })

    # Stale-While-Revalidate: Dispatch background thread without blocking HTTP response
    if needs_sync and holdings and not force:
        dispatch_background_sync(background_sync_portfolio_prices, portfolio_id, 30)

    for c, tdata in totals_by_currency.items():
        t_c = tdata["cost"]
        t_m = tdata["market_value"]
        t_pnl = round(t_m - t_c, 2) if t_c > 0 else 0.0
        t_pnl_pct = round((t_pnl / t_c) * 100, 2) if t_c > 0 else 0.0
        tdata["pnl"] = t_pnl
        tdata["pnl_pct"] = t_pnl_pct

    active_currencies = [c for c, d in totals_by_currency.items() if d["cost"] > 0 or d["market_value"] > 0]
    if not active_currencies and holdings:
        active_currencies = list({h.get("currency", "USD") for h in holdings})
    is_mixed_currency = len(active_currencies) > 1
    primary_currency = active_currencies[0] if len(active_currencies) == 1 else "USD"

    total_shares = sum(h['shares'] for h in holdings)
    total_pnl = round(total_market_value - total_cost, 2) if total_cost > 0 else 0.0
    total_pnl_pct = round((total_pnl / total_cost) * 100, 2) if total_cost > 0 else 0.0

    payload = {
        "status": "success",
        "last_updated": latest_update.strftime('%H:%M:%S') if latest_update else "Live",
        "summary": {
            "total_holdings": len(results),
            "total_shares": round(total_shares, 2),
            "total_cost": total_cost,
            "total_market_value": total_market_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "totals_by_currency": totals_by_currency,
            "is_mixed_currency": is_mixed_currency,
            "primary_currency": primary_currency,
        },
        "holdings": results
    }

    # Cache response for 10 seconds to serve rapid consecutive polls in <1ms
    cache.set(cache_key, payload, ttl=10)
    return payload


@router.post("/portfolio/{portfolio_id}/analyze-ai", name="analyze_portfolio_ai", dependencies=[Depends(verify_csrf)], response_model=None)
def analyze_portfolio_ai(
    portfolio_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
        portfolio = cur.fetchone()
        if not portfolio:
            flash(request, "Portfolio not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

        cur.execute("SELECT COUNT(*) as count FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
        cnt = cur.fetchone()['count']
        if cnt == 0:
            flash(request, "Cannot analyze an empty portfolio. Add holdings first.", "warning")
            return RedirectResponse(url=f"/portfolio/{portfolio_id}", status_code=303)

    # Invalidate cached views
    cache.delete(f"data:portfolio_view:{current_user.id}:{portfolio_id}")
    cache.delete(f"data:user:{current_user.id}:dashboard")

    try:
        from app.services.workflow import run_root_workflow
        run_root_workflow(portfolio_id=portfolio_id)
        flash(request, f"AI analysis completed for {portfolio['name']}! Diagnostic alerts updated.", "success")
    except Exception as e:
        flash(request, f"AI analysis error: {e}", "error")

    return RedirectResponse(url=f"/portfolio/{portfolio_id}", status_code=303)

