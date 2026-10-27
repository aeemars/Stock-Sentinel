import math
import re
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from psycopg2.extras import RealDictCursor
from db import get_db
from app.services.cache import cache
from app.services.market import fetch_single_ticker_price, normalize_crypto_ticker, is_crypto_ticker
from app.dependencies import get_current_user, verify_csrf
from app.session import flash

router = APIRouter(tags=["holdings"])

TICKER_REGEX = re.compile(r"^[A-Z0-9.\-:]{1,20}$")

@router.post("/portfolio/{portfolio_id}/holding/create", name="create_holding", dependencies=[Depends(verify_csrf)], response_model=None)
def create_holding(
    portfolio_id: int,
    request: Request,
    ticker: str = Form(""),
    shares: str = Form(""),
    buy_price: str = Form(""),
    currency: str = Form(""),
    current_user=Depends(get_current_user),
    conn=Depends(get_db)
):
    ticker = ticker.strip().upper()
    ticker = normalize_crypto_ticker(ticker)
    shares_str = shares.strip()
    buy_price_str = buy_price.strip() if buy_price else ""

    if not ticker or not shares_str:
        flash(request, "Ticker and shares are required", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    if not TICKER_REGEX.match(ticker):
        flash(request, "Invalid ticker symbol format. Use 1-20 alphanumeric characters (e.g. AAPL, BTC-USD).", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    try:
        shares_val = float(shares_str)
        if not math.isfinite(shares_val) or shares_val <= 0 or shares_val > 10_000_000:
            raise ValueError("Shares must be a positive number up to 10,000,000.")
    except (ValueError, TypeError) as e:
        flash(request, f"Invalid shares value: {str(e)}", "error")
        return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    # Parse optional user-specified buy price
    entry_price = None
    if buy_price_str:
        try:
            parsed_bp = float(buy_price_str)
            if not math.isfinite(parsed_bp) or parsed_bp <= 0 or parsed_bp > 10_000_000:
                flash(request, "Buy price must be a positive number up to $10,000,000.", "error")
                return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)
            entry_price = parsed_bp
        except (ValueError, TypeError):
            flash(request, "Invalid buy price format", "error")
            return RedirectResponse(url=str(request.url_for("view_portfolio", portfolio_id=portfolio_id)), status_code=303)

    # Fetch current dynamic market price from yfinance service
    market_price = fetch_single_ticker_price(ticker)

    # Fallback entry_price to market_price if user left it blank
    if entry_price is None:
        entry_price = market_price

    holding_id = None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, current_user.id))
            if not cur.fetchone():
                flash(request, "Portfolio not found", "error")
                return RedirectResponse(url=str(request.url_for("dashboard")), status_code=303)

            shares_delta = shares_val
            cur.execute("""
                SELECT id, shares, buy_price, last_price FROM holdings
                WHERE portfolio_id = %s AND ticker = %s
            """, (portfolio_id, ticker))
            existing_row = cur.fetchone()

            if existing_row:
                shares_prev = existing_row['shares']
                shares_total = shares_prev + shares_delta
                prev_buy_price = existing_row.get('buy_price') or existing_row.get('last_price') or entry_price

                # Calculate weighted average buy price
                if entry_price is not None:
                    if prev_buy_price is not None and shares_prev > 0:
                        new_buy_price = round(((shares_prev * prev_buy_price) + (shares_delta * entry_price)) / shares_total, 4)
                    else:
                        new_buy_price = entry_price
                else:
                    new_buy_price = prev_buy_price

                cur.execute("""
                    UPDATE holdings
                    SET shares = %s,
                        buy_price = %s,
                        last_price = COALESCE(%s, last_price),
                        last_price_updated_at = CASE WHEN %s IS NOT NULL THEN CURRENT_TIMESTAMP ELSE last_price_updated_at END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (shares_total, new_buy_price, market_price, market_price, existing_row['id']))

                value_before = shares_prev * prev_buy_price if prev_buy_price is not None else None
                value_after = shares_total * new_buy_price if new_buy_price is not None else None

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
                    entry_price,
                    value_before,
                    value_after,
                ))
                holding_id = existing_row['id']
            else:
                resolved_currency = "USD"
                initial_last_price = market_price if market_price is not None else entry_price
                cur.execute("""
                    INSERT INTO holdings (portfolio_id, ticker, shares, buy_price, currency, last_price, last_price_updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END) RETURNING id
                """, (portfolio_id, ticker, shares_delta, entry_price, resolved_currency, initial_last_price, initial_last_price))
                new_holding = cur.fetchone()
                holding_id = new_holding['id']

                value_before = 0.0
                value_after = shares_delta * entry_price if entry_price is not None else None

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
                    entry_price,
                    value_before,
                    value_after,
                ))

        conn.commit()
        cache.delete_by_prefix(f"data:user:{current_user.id}:")
        cache.delete(f"data:live_prices:{current_user.id}:{portfolio_id}")
        cache.delete(f"data:live_prices:{portfolio_id}")

        unit_label = "units" if is_crypto_ticker(ticker) else "shares"
        price_str = f" @ ${entry_price:.2f}" if entry_price is not None else ""
        flash(request, f"Added {shares_val} {unit_label} of {ticker}{price_str} to portfolio", "success")
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
        cache.delete_by_prefix(f"data:user:{current_user.id}:")
        if portfolio_id:
            cache.delete(f"data:live_prices:{current_user.id}:{portfolio_id}")
            cache.delete(f"data:live_prices:{portfolio_id}")
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
