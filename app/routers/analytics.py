from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user
from app.templates_config import render_template

router = APIRouter(tags=["analytics"])

@router.get("/analytics", name="analytics", response_model=None)
def analytics(
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # ── Portfolios ──
        cur.execute("SELECT id, name FROM portfolios WHERE user_id = %s ORDER BY id", (current_user.id,))
        portfolios = cur.fetchall()
        portfolio_ids = [p['id'] for p in portfolios]

        # ── All holdings with prices ──
        holdings = []
        if portfolio_ids:
            cur.execute("""
                SELECT h.id, h.ticker, h.shares, h.last_price, h.portfolio_id, p.name as portfolio_name
                FROM holdings h
                JOIN portfolios p ON h.portfolio_id = p.id
                WHERE p.user_id = %s
                ORDER BY h.ticker
            """, (current_user.id,))
            holdings = cur.fetchall()

        # ── Compute per-holding market value ──
        for h in holdings:
            h['market_value'] = round(h['shares'] * h['last_price'], 2) if h['last_price'] else 0.0

        total_value = sum(h['market_value'] for h in holdings)
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

    return render_template(
        "analytics.html",
        request,
        {
            "total_value": total_value,
            "total_holdings": total_holdings,
            "unique_tickers": unique_tickers,
            "portfolio_stats": portfolio_stats,
            "ticker_distribution": ticker_distribution,
            "alert_history": alert_history,
            "holdings": holdings,
        }
    )
