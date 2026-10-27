from datetime import datetime, timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user
from app.services.cache import cache
from app.services.market import background_sync_user_prices, is_ngx_ticker
from app.templates_config import render_template

router = APIRouter(tags=["analytics"])

@router.get("/analytics", name="analytics", response_model=None)
def analytics(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    cache_key = f"data:user:{current_user.id}:analytics"
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return render_template("analytics.html", request, cached_payload)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # ── Portfolios (indexed query) ──
        cur.execute("SELECT id, name FROM portfolios WHERE user_id = %s ORDER BY id", (current_user.id,))
        portfolios = cur.fetchall()
        portfolio_ids = [p['id'] for p in portfolios]

        # Stale-While-Revalidate: queue background price sync without blocking response
        if portfolio_ids:
            background_tasks.add_task(background_sync_user_prices, current_user.id, 120)

        # ── All holdings with prices (indexed query) ──
        holdings = []
        if portfolio_ids:
            cur.execute("""
                SELECT h.id, h.ticker, h.shares, h.currency, h.last_price, h.portfolio_id, p.name as portfolio_name
                FROM holdings h
                JOIN portfolios p ON h.portfolio_id = p.id
                WHERE p.user_id = %s
                ORDER BY h.ticker
            """, (current_user.id,))
            holdings = cur.fetchall()

        # ── Compute per-holding market value & currency breakdown ──
        totals_by_currency = {}
        for h in holdings:
            curr = h.get('currency') or ('NGN' if is_ngx_ticker(h['ticker']) else 'USD')
            h['currency'] = curr
            mv = round(h['shares'] * h['last_price'], 2) if h['last_price'] else 0.0
            h['market_value'] = mv
            if curr not in totals_by_currency:
                totals_by_currency[curr] = {"value": 0.0, "symbol": "₦" if curr == "NGN" else "$"}
            totals_by_currency[curr]["value"] += mv

        is_mixed_currency = len(totals_by_currency) > 1
        primary_currency = list(totals_by_currency.keys())[0] if totals_by_currency else "USD"
        total_value = sum(c["value"] for c in totals_by_currency.values())
        total_holdings = len(holdings)
        unique_tickers = len(set(h['ticker'] for h in holdings))

        # ── Holdings by portfolio (bar chart data) ──
        portfolio_stats = []
        for p in portfolios:
            count = sum(1 for h in holdings if h['portfolio_id'] == p['id'])
            value = sum(h['market_value'] for h in holdings if h['portfolio_id'] == p['id'])
            portfolio_stats.append({
                'name': p['name'],
                'holdings': count,
                'value': round(value, 2)
            })

        # ── Ticker distribution (donut chart data) ──
        ticker_map = {}
        for h in holdings:
            t = h['ticker']
            if t in ticker_map:
                ticker_map[t]['value'] += h['market_value']
                ticker_map[t]['shares'] += h['shares']
            else:
                ticker_map[t] = {
                    'ticker': t,
                    'value': h['market_value'],
                    'shares': h['shares'],
                    'price': h['last_price'] or 0,
                }
        ticker_distribution = sorted(ticker_map.values(), key=lambda x: x['value'], reverse=True)

        # ── Alerts over last 7 days (line chart data) ──
        alert_history = []
        if portfolio_ids:
            cur.execute("""
                SELECT DATE(a.created_at) as day, COUNT(*) as count
                FROM alerts a
                JOIN portfolios p ON a.portfolio_id = p.id
                WHERE p.user_id = %s
                  AND a.created_at >= CURRENT_DATE - INTERVAL '6 days'
                GROUP BY DATE(a.created_at)
                ORDER BY day
            """, (current_user.id,))
            alert_rows = cur.fetchall()
            alert_map = {str(r['day']): r['count'] for r in alert_rows}

            for i in range(7):
                day = (datetime.now() - timedelta(days=6 - i)).strftime('%Y-%m-%d')
                alert_history.append({
                    'day': day,
                    'label': (datetime.now() - timedelta(days=6 - i)).strftime('%a'),
                    'count': alert_map.get(day, 0)
                })

        payload = {
            "total_value": total_value,
            "total_holdings": total_holdings,
            "unique_tickers": unique_tickers,
            "totals_by_currency": totals_by_currency,
            "is_mixed_currency": is_mixed_currency,
            "primary_currency": primary_currency,
            "portfolio_stats": portfolio_stats,
            "ticker_distribution": ticker_distribution,
            "alert_history": alert_history,
            "holdings": holdings,
        }
        cache.set(cache_key, payload, ttl=45.0)

    return render_template("analytics.html", request, payload)
