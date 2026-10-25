import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple
import yfinance as yf
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# In-memory thread-safe short-term price cache to prevent duplicate external requests
_CACHE_LOCK = threading.Lock()
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}  # ticker -> (price, timestamp)
DEFAULT_CACHE_TTL = 30.0  # seconds

# Recognized Nigerian Exchange (NGX) equities for native Naira (NGN) resolution
KNOWN_NGX_TICKERS: Set[str] = {
    "MTNN", "DANGCEM", "AIRTELAFRI", "BUAFOODS", "BUACEMENT", "ZENITHBANK",
    "GTCO", "ACCESSCORP", "UBA", "FBNH", "SEPLAT", "NESTLE", "PRESCO",
    "OKOMUOIL", "TRANSCORP", "TRANSCORPPOWER", "GEREGU", "STANBIC", "FIDELITYBK",
    "FCMB", "STERLINGNG", "WAPCO", "GUINNESS", "NB", "TOTAL", "CONOIL",
    "OANDO", "FLOURMILL", "UNILEVER", "CADBURY", "JAIZBANK", "WEMABANK",
    "CUSTODIAN", "AIICO", "MANSARD", "NAHCO", "CHAMS", "CWG", "ETRANZACT",
    "INTBREW", "VITAFOAM", "DANGSUGAR", "NASCON", "UCAP", "NGXGROUP",
    "CORNERST", "VERITASKAP", "SOVRENINS", "CHIPLC", "SUNUASSUR", "ROYALEX"
}


def is_ngx_ticker(ticker: str) -> bool:
    """Check if a ticker represents a Nigerian Exchange (NGX) stock."""
    if not ticker:
        return False
    clean = ticker.strip().upper()
    if clean.endswith(".NG") or clean.endswith(".LG") or clean.startswith("NGX:"):
        return True
    return clean in KNOWN_NGX_TICKERS


def normalize_ngx_symbol(ticker: str) -> str:
    """Normalize NGX ticker to standard base symbol (e.g. DANGCEM.NG -> DANGCEM)."""
    clean = ticker.strip().upper()
    if clean.startswith("NGX:"):
        clean = clean[4:]
    if clean.endswith(".NG") or clean.endswith(".LG"):
        clean = clean.rsplit(".", 1)[0]
    return clean


def get_ticker_currency(ticker: str) -> str:
    """Determine currency based on symbol: NGN for Nigerian stocks, USD for others."""
    return "NGN" if is_ngx_ticker(ticker) else "USD"


def get_cached_price(ticker: str, ttl: float = DEFAULT_CACHE_TTL) -> Optional[float]:
    ticker = ticker.strip().upper()
    now = time.time()
    with _CACHE_LOCK:
        if ticker in _PRICE_CACHE:
            price, ts = _PRICE_CACHE[ticker]
            if (now - ts) <= ttl:
                return price
    return None


def set_cached_price(ticker: str, price: float) -> None:
    ticker = ticker.strip().upper()
    with _CACHE_LOCK:
        _PRICE_CACHE[ticker] = (round(float(price), 4), time.time())


def fetch_kobo_ticker_price(ticker: str, ttl: float = DEFAULT_CACHE_TTL) -> Optional[float]:
    """
    Fetch live dynamic market price for a Nigerian stock from Kobo Terminal API.
    Shares the same cache and provides graceful fallbacks.
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    cached = get_cached_price(clean_ticker, ttl=ttl)
    if cached is not None:
        return cached

    symbol = normalize_ngx_symbol(clean_ticker)

    from app.config import Settings
    settings = Settings()
    api_key = settings.effective_kobo_api_key
    base_url = (settings.KOBO_TERMINAL_BASE_URL or "https://koboterminal.com").rstrip("/")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Stock-Sentinel/1.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"

    # Official Kobo Terminal NGX endpoint: /api/ngxdata/prices/:symbol
    endpoints = [
        f"{base_url}/api/ngxdata/prices/{symbol}",
        f"{base_url}/api/ngxdata/fundamentals/{symbol}",
        f"{base_url}/api/stocks/{symbol}",
        f"{base_url}/stocks/{symbol}",
    ]

    price = None
    try:
        import httpx
        with httpx.Client(timeout=8.0) as client:
            for url in endpoints:
                try:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidate = None
                        if isinstance(data, dict):
                            inner = data.get("data") if isinstance(data.get("data"), dict) else data
                            for key in ("current_price", "price", "last_price", "regularMarketPrice", "close", "last"):
                                val = inner.get(key)
                                if val is not None:
                                    try:
                                        cand_val = float(val)
                                        if cand_val > 0:
                                            candidate = cand_val
                                            break
                                    except (ValueError, TypeError):
                                        continue
                        if candidate is not None:
                            price = candidate
                            break
                    elif resp.status_code in (401, 403, 404):
                        continue
                except Exception as req_err:
                    logger.debug(f"Kobo endpoint {url} attempt failed: {req_err}")
                    continue
    except Exception as exc:
        logger.warning(f"Error fetching Kobo Terminal price for {symbol}: {exc}")

    if price is not None and price > 0:
        price = round(float(price), 4)
        set_cached_price(clean_ticker, price)
        if symbol != clean_ticker:
            set_cached_price(symbol, price)
            set_cached_price(f"{symbol}.NG", price)
        return price

    return None


def fetch_single_ticker_price(ticker: str, ttl: float = DEFAULT_CACHE_TTL) -> Optional[float]:
    """
    Fetch current dynamic market price for a single ticker.
    Routes Nigerian stocks (NGN) to Kobo Terminal API and US/global stocks (USD) to YFinance.
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    cached = get_cached_price(clean_ticker, ttl=ttl)
    if cached is not None:
        return cached

    # Smart Routing: Nigerian Exchange tickers -> Kobo Terminal API
    if is_ngx_ticker(clean_ticker):
        return fetch_kobo_ticker_price(clean_ticker, ttl=ttl)

    # Global / US Equities -> Yahoo Finance
    price = None
    try:
        t = yf.Ticker(clean_ticker)
        # fast_info is rapid, avoiding heavy full-table downloads
        fast_info = getattr(t, "fast_info", None)
        if fast_info:
            price = fast_info.get("lastPrice")
            if price is None or price <= 0:
                price = fast_info.get("regularMarketPrice")
            if price is None or price <= 0:
                price = fast_info.get("previousClose")

        # Fallback to 1-day download if fast_info didn't resolve
        if price is None or price <= 0:
            df = yf.download(clean_ticker, period="1d", progress=False, multi_level_index=False)
            if not df.empty and "Close" in df.columns:
                price = float(df["Close"].iloc[-1])
    except Exception as exc:
        logger.warning(f"Error fetching live market price for ticker {clean_ticker}: {exc}")

    if price is not None and price > 0:
        price = round(float(price), 4)
        set_cached_price(clean_ticker, price)
        return price

    return None


def fetch_latest_prices(tickers: List[str], max_workers: int = 6) -> Dict[str, float]:
    """Concurrently fetch latest market prices for a list of tickers."""
    cleaned = list({t.strip().upper() for t in tickers if t and t.strip()})
    if not cleaned:
        return {}

    results: Dict[str, float] = {}
    missing: List[str] = []

    # Check cache first
    for ticker in cleaned:
        cached = get_cached_price(ticker)
        if cached is not None:
            results[ticker] = cached
        else:
            missing.append(ticker)

    if not missing:
        return results

    # Fetch missing tickers concurrently
    workers = min(len(missing), max_workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_single_ticker_price, ticker): ticker for ticker in missing}
        for future in futures:
            ticker = futures[future]
            try:
                price = future.result()
                if price is not None:
                    results[ticker] = price
            except Exception as exc:
                logger.warning(f"Worker failed fetching price for {ticker}: {exc}")

    return results


def sync_portfolio_prices(
    portfolio_id: int,
    conn,
    max_age_seconds: int = 60,
    force: bool = False
) -> Dict[str, float]:
    """
    Check if portfolio holdings have stale or missing prices.
    If so, fetches dynamic latest prices from yfinance and updates the database.
    Returns a dictionary of ticker -> price.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if force:
            cur.execute("""
                SELECT id, ticker, last_price, last_price_updated_at
                FROM holdings
                WHERE portfolio_id = %s
            """, (portfolio_id,))
        else:
            cur.execute("""
                SELECT id, ticker, last_price, last_price_updated_at
                FROM holdings
                WHERE portfolio_id = %s
                  AND (
                      last_price IS NULL
                      OR last_price_updated_at IS NULL
                      OR last_price_updated_at < (NOW() - (%s || ' seconds')::INTERVAL)
                  )
            """, (portfolio_id, str(max_age_seconds)))

        stale_holdings = cur.fetchall()

    if not stale_holdings:
        return {}

    tickers = list({row["ticker"] for row in stale_holdings})
    new_prices = fetch_latest_prices(tickers)

    if not new_prices:
        return {}

    try:
        with conn.cursor() as cur:
            for row in stale_holdings:
                price = new_prices.get(row["ticker"])
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
        conn.commit()
        from app.services.cache import cache
        cache.delete(f"data:live_prices:{portfolio_id}")
    except Exception as exc:
        conn.rollback()
        logger.error(f"Failed to update stale holding prices for portfolio {portfolio_id}: {exc}")

    return new_prices


def sync_user_holdings_prices(
    user_id: int,
    conn,
    max_age_seconds: int = 120
) -> Dict[str, float]:
    """Sync prices for all holdings belonging to a user across all portfolios if stale."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT h.id, h.ticker, h.last_price, h.last_price_updated_at
            FROM holdings h
            JOIN portfolios p ON h.portfolio_id = p.id
            WHERE p.user_id = %s
              AND (
                  h.last_price IS NULL
                  OR h.last_price_updated_at IS NULL
                  OR h.last_price_updated_at < (NOW() - (%s || ' seconds')::INTERVAL)
              )
        """, (user_id, str(max_age_seconds)))
        stale_holdings = cur.fetchall()

    if not stale_holdings:
        return {}

    tickers = list({row["ticker"] for row in stale_holdings})
    new_prices = fetch_latest_prices(tickers)

    if not new_prices:
        return {}

    try:
        with conn.cursor() as cur:
            for row in stale_holdings:
                price = new_prices.get(row["ticker"])
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error(f"Failed to update stale user holding prices for user {user_id}: {exc}")

    return new_prices


def calculate_holding_metrics(holding: dict) -> dict:
    """Calculate market value, cost basis, profit/loss, and return on holding."""
    shares = float(holding.get("shares") or 0.0)
    buy_price = holding.get("buy_price")
    last_price = holding.get("last_price")

    cost_basis = round(shares * float(buy_price), 2) if buy_price is not None else 0.0
    market_val = round(shares * float(last_price), 2) if last_price is not None else 0.0

    pnl = None
    pnl_pct = None
    if buy_price is not None and last_price is not None:
        pnl = round(market_val - cost_basis, 2)
        pnl_pct = round((pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0

    return {
        "cost_basis": cost_basis,
        "market_value": market_val,
        "pnl": pnl,
        "pnl_pct": pnl_pct
    }


_ACTIVE_PORTFOLIO_SYNCS = set()
_ACTIVE_USER_SYNCS = set()
_SYNC_LOCK = threading.Lock()


def background_sync_portfolio_prices(portfolio_id: int, max_age_seconds: int = 60, force: bool = False):
    """Run portfolio price synchronization in background without holding DB connections during network calls."""
    with _SYNC_LOCK:
        if portfolio_id in _ACTIVE_PORTFOLIO_SYNCS:
            return
        _ACTIVE_PORTFOLIO_SYNCS.add(portfolio_id)

    from db import get_db_connection, put_db_connection
    from app.services.cache import cache

    try:
        # Phase 1: Query stale holdings and release DB connection immediately (<1ms)
        conn = get_db_connection()
        stale_holdings = []
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if force:
                    cur.execute("SELECT id, ticker FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
                else:
                    cur.execute("""
                        SELECT id, ticker FROM holdings
                        WHERE portfolio_id = %s
                          AND (
                              last_price IS NULL
                              OR last_price_updated_at IS NULL
                              OR last_price_updated_at < (NOW() - (%s || ' seconds')::INTERVAL)
                          )
                    """, (portfolio_id, str(max_age_seconds)))
                stale_holdings = cur.fetchall()
        finally:
            put_db_connection(conn)

        if not stale_holdings:
            return

        # Phase 2: External network fetch (ZERO DB connections held during Yahoo Finance requests)
        tickers = list({row["ticker"] for row in stale_holdings})
        new_prices = fetch_latest_prices(tickers)
        if not new_prices:
            return

        # Phase 3: Check out DB connection briefly to apply updates and commit (<1ms)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for row in stale_holdings:
                    price = new_prices.get(row["ticker"])
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
            conn.commit()
            cache.delete(f"data:live_prices:{portfolio_id}")
            cache.delete_by_prefix("data:portfolio_view:")
            cache.delete_by_prefix("data:user:")
        finally:
            put_db_connection(conn)

    except Exception as exc:
        logger.warning(f"Background price sync failed for portfolio {portfolio_id}: {exc}")
    finally:
        with _SYNC_LOCK:
            _ACTIVE_PORTFOLIO_SYNCS.discard(portfolio_id)


def background_sync_user_prices(user_id: int, max_age_seconds: int = 120):
    """Run user holdings price synchronization in background without holding DB connections during network calls."""
    with _SYNC_LOCK:
        if user_id in _ACTIVE_USER_SYNCS:
            return
        _ACTIVE_USER_SYNCS.add(user_id)

    from db import get_db_connection, put_db_connection
    from app.services.cache import cache

    try:
        conn = get_db_connection()
        stale_holdings = []
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT h.id, h.ticker FROM holdings h
                    JOIN portfolios p ON h.portfolio_id = p.id
                    WHERE p.user_id = %s
                      AND (
                          h.last_price IS NULL
                          OR h.last_price_updated_at IS NULL
                          OR h.last_price_updated_at < (NOW() - (%s || ' seconds')::INTERVAL)
                      )
                """, (user_id, str(max_age_seconds)))
                stale_holdings = cur.fetchall()
        finally:
            put_db_connection(conn)

        if not stale_holdings:
            return

        tickers = list({row["ticker"] for row in stale_holdings})
        new_prices = fetch_latest_prices(tickers)
        if not new_prices:
            return

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for row in stale_holdings:
                    price = new_prices.get(row["ticker"])
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
            conn.commit()
            cache.delete_by_prefix(f"data:user:{user_id}:")
            cache.delete_by_prefix("data:live_prices:")
            cache.delete_by_prefix("data:portfolio_view:")
        finally:
            put_db_connection(conn)

    except Exception as exc:
        logger.warning(f"Background price sync failed for user {user_id}: {exc}")
    finally:
        with _SYNC_LOCK:
            _ACTIVE_USER_SYNCS.discard(user_id)


def dispatch_background_sync(target_func, *args):
    """Launch detached daemon thread so external network calls never block HTTP response."""
    thread = threading.Thread(target=target_func, args=args, daemon=True)
    thread.start()


def fetch_price_history(ticker: str, period: str = '7d', interval: str = '1d'):
    """Fetch recent price history dataframe for a ticker."""
    df = yf.download(ticker, period=period, interval=interval, progress=False, multi_level_index=False)
    if df is None or df.empty:
        return None
    df = df.reset_index()[['Date', 'Close']].rename(columns={'Date': 'ds', 'Close': 'y'})
    return df


class PriceAgent:
    """Historical price fetcher service."""
    def fetch(self, ticker: str, period: str = '7d', interval: str = '1d'):
        return fetch_price_history(ticker, period=period, interval=interval)



