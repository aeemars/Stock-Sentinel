from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.dependencies import get_current_user, verify_csrf, is_safe_redirect
from app.session import flash
from app.templates_config import render_template

router = APIRouter(tags=["alerts"])

@router.get("/alerts", name="alerts", response_model=None)
def alerts_page(
    request: Request,
    portfolio_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT a.* FROM alerts a
            JOIN portfolios p ON a.portfolio_id = p.id
            WHERE p.user_id = %s
        """
        params = [current_user.id]

        if portfolio_id:
            query += " AND a.portfolio_id = %s"
            params.append(portfolio_id)

        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                query += " AND a.created_at >= %s"
                params.append(date_from_obj)
            except ValueError:
                pass

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                query += " AND a.created_at < %s"
                params.append(date_to_obj)
            except ValueError:
                pass

        query += " ORDER BY a.created_at DESC LIMIT 50"

        cur.execute(query, tuple(params))
        alerts_list = cur.fetchall()

        # Enrich alerts with portfolio names
        for alert in alerts_list:
            if alert['portfolio_id']:
                cur.execute("SELECT name FROM portfolios WHERE id = %s", (alert['portfolio_id'],))
                port = cur.fetchone()
                alert['portfolio_name'] = port['name'] if port else 'Unknown'
            else:
                alert['portfolio_name'] = 'Unknown'

        cur.execute("SELECT * FROM portfolios WHERE user_id = %s", (current_user.id,))
        all_portfolios = cur.fetchall()

    return render_template(
        "alerts.html",
        request,
        {
            "alerts": alerts_list,
            "all_portfolios": all_portfolios,
            "selected_portfolio": portfolio_id,
            "date_from": date_from,
            "date_to": date_to,
        }
    )

@router.post("/alert/{alert_id}/dismiss", name="dismiss_alert", dependencies=[Depends(verify_csrf)], response_model=None)
def dismiss_alert(
    alert_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Verify ownership
            cur.execute("""
                SELECT a.id FROM alerts a
                JOIN portfolios p ON a.portfolio_id = p.id
                WHERE a.id = %s AND p.user_id = %s
            """, (alert_id, current_user.id))
            if cur.fetchone():
                cur.execute("DELETE FROM alerts WHERE id = %s", (alert_id,))
                conn.commit()
                flash(request, "Alert dismissed", "success")
            else:
                flash(request, "Alert not found", "error")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error dismissing alert: {str(e)}", "error")

    referer = request.headers.get("referer")
    dest = referer if is_safe_redirect(request, referer) else str(request.url_for("alerts"))
    return RedirectResponse(url=dest, status_code=303)
