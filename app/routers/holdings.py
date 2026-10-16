from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
import yfinance as yf
from db import get_db
from app.dependencies import get_current_user, verify_csrf
from app.session import flash

router = APIRouter(tags=["holdings"])

@router.post("/portfolio/{portfolio_id}/holding/create", name="create_holding", dependencies=[Depends(verify_csrf)], response_model=None)
def create_holding(
    portfolio_id: int,
    request: Request,
    ticker: str = Form(""),
    shares: str = Form(""),
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    ticker = ticker.strip().upper()
    shares_str = shares.strip()

    if not ticker or not shares_str:
        flash(request, "Ticker and shares are required", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    try:
        shares_val = float(shares_str)
        if shares_val <= 0:
            raise ValueError("Shares must be positive")
    except ValueError as e:
        flash(request, f"Invalid shares value: {str(e)}", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    snapshot_price = None
    try:
        snapshot_price = yf.Ticker(ticker).fast_info.get('lastPrice')
        if not snapshot_price:
            df = yf.download(ticker, period='1d', progress=False, multi_level_index=False)
            if not df.empty:
                snapshot_price = float(df['Close'].iloc[-1])
    except Exception:
        snapshot_price = None

    holding_id = None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
            if not cur.fetchone():
                flash(request, "Portfolio not found", "error")
                return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

            shares_delta = shares_val
            cur.execute("""
                SELECT id, shares FROM holdings
                WHERE portfolio_id = %s AND ticker = %s
            """, (portfolio_id, ticker))
            existing_row = cur.fetchone()

            if existing_row:
                shares_prev = existing_row['shares']
                shares_total = shares_prev + shares_delta

                cur.execute("""
                    UPDATE holdings
                    SET shares = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (shares_total, existing_row['id']))

                value_before = shares_prev * snapshot_price if snapshot_price is not None else None
                value_after = shares_total * snapshot_price if snapshot_price is not None else None

                cur.execute("""
                    INSERT INTO holding_snapshots
                        (holding_id, portfolio_id, ticker, event,
                         shares_delta, shares_total, price_at_event, value_before, value_after)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    existing_row['id'],
                    portfolio_id,
                    ticker,
                    'add',
                    shares_delta,
                    shares_total,
                    snapshot_price,
                    value_before,
                    value_after,
                ))
                holding_id = existing_row['id']
            else:
                cur.execute("""
                    INSERT INTO holdings (portfolio_id, ticker, shares)
                    VALUES (%s, %s, %s) RETURNING id
                """, (portfolio_id, ticker, shares_delta))
                new_holding = cur.fetchone()
                holding_id = new_holding['id']

                value_before = 0.0
                value_after = shares_delta * snapshot_price if snapshot_price is not None else None

                cur.execute("""
                    INSERT INTO holding_snapshots
                        (holding_id, portfolio_id, ticker, event,
                         shares_delta, shares_total, price_at_event, value_before, value_after)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    holding_id,
                    portfolio_id,
                    ticker,
                    'initial',
                    shares_delta,
                    shares_delta,
                    snapshot_price,
                    value_before,
                    value_after,
                ))

        conn.commit()

        if snapshot_price is not None and holding_id is not None:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE holdings
                       SET last_price = %s, last_price_updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (snapshot_price, holding_id)
                )
            conn.commit()

        price_str = f" @ ${snapshot_price:.2f}" if snapshot_price is not None else ""
        flash(request, f"Added {shares_val} shares of {ticker}{price_str} to portfolio", "success")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error adding holding: {str(e)}", "error")

    return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

@router.post("/holding/{holding_id}/delete", name="delete_holding", dependencies=[Depends(verify_csrf)], response_model=None)
def delete_holding(
    holding_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    portfolio_id = None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT h.portfolio_id, h.ticker FROM holdings h
                JOIN portfolios p ON h.portfolio_id = p.id
                WHERE h.id = %s AND p.user_id = %s
            """, (holding_id, current_user.id))
            holding = cur.fetchone()

            if not holding:
                flash(request, "Holding not found", "error")
                return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

            portfolio_id = holding['portfolio_id']
            ticker = holding['ticker']

            cur.execute("DELETE FROM holdings WHERE id = %s", (holding_id,))

        conn.commit()
        flash(request, f"Removed {ticker} from portfolio", "success")
    except Exception as e:
        conn.rollback()
        flash(request, f"Error deleting holding: {str(e)}", "error")

    if portfolio_id:
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

@router.get("/holding/{holding_id}/edit", name="edit_holding", response_model=None)
def edit_holding(
    holding_id: int,
    request: Request,
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    portfolio_id = None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT h.portfolio_id FROM holdings h
            JOIN portfolios p ON h.portfolio_id = p.id
            WHERE h.id = %s AND p.user_id = %s
        """, (holding_id, current_user.id))
        holding = cur.fetchone()

        if not holding:
            flash(request, "Holding not found", "error")
            return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

        portfolio_id = holding['portfolio_id']

    if portfolio_id:
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)
    return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)
