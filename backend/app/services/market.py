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
_NEGATIVE_CACHE: Dict[str, float] = {}  # ticker -> expire_timestamp
DEFAULT_CACHE_TTL = 30.0  # seconds for US / Yahoo Finance
DEFAULT_CACHE_TTL = 30.0  # seconds for US equities and crypto / Yahoo Finance

# Well-known crypto base symbols for smart detection
KNOWN_CRYPTO_BASES: Set[str] = {
    "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "DOT", "MATIC",
    "LINK", "UNI", "SHIB", "LTC", "BCH", "ATOM", "FIL", "APT", "ARB",
    "OP", "NEAR", "ALGO", "AAVE", "CRO", "MANA", "SAND", "AXS", "FTM",
    "HBAR", "ICP", "VET", "EOS", "XLM", "TRX", "BNB", "PEPE", "SUI",
}


def is_crypto_ticker(ticker: str) -> bool:
    """Detect cryptocurrency tickers (e.g., BTC-USD, ETH-EUR, BTC)."""
    if not ticker:
        return False
    clean = ticker.strip().upper()
    if "-" in clean:
        base, quote = clean.rsplit("-", 1)
        if quote in ("USD", "EUR", "GBP", "USDT", "USDC", "BTC"):
            return True
        if base in KNOWN_CRYPTO_BASES:
            return True
    return clean in KNOWN_CRYPTO_BASES


def normalize_crypto_ticker(ticker: str) -> str:
    """Ensure crypto tickers have a quote currency (default: -USD)."""
    if not ticker:
        return ""
    clean = ticker.strip().upper()
    if clean in KNOWN_CRYPTO_BASES and "-" not in clean:
        return f"{clean}-USD"
    return clean


def get_asset_type(ticker: str) -> str:
    """Return 'crypto' or 'stock' for a given ticker."""
    return "crypto" if is_crypto_ticker(ticker) else "stock"


def get_ticker_currency(ticker: str = "") -> str:
    """Return default currency for assets (USD). Maintained for compatibility."""
    return "USD"


def get_cached_price(ticker: str, ttl: Optional[float] = None) -> Optional[float]:
    ticker = ticker.strip().upper()
    if ttl is None:
        ttl = DEFAULT_CACHE_TTL
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


def fetch_single_ticker_price(ticker: str, ttl: float = DEFAULT_CACHE_TTL, force: bool = False) -> Optional[float]:
    """
    Fetch current dynamic market price for a single ticker (stock or crypto) via Yahoo Finance.
    Automatically normalizes crypto tickers (e.g. BTC -> BTC-USD).
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    # Auto-normalize bare crypto base (e.g. BTC -> BTC-USD)
    lookup_ticker = normalize_crypto_ticker(clean_ticker)

    if not force:
        cached = get_cached_price(clean_ticker, ttl=ttl) or get_cached_price(lookup_ticker, ttl=ttl)
        if cached is not None:
            return cached

    # Check negative cache
    now = time.time()
    with _CACHE_LOCK:
        neg_expire = _NEGATIVE_CACHE.get(lookup_ticker) or _NEGATIVE_CACHE.get(clean_ticker)
        if neg_expire and now < neg_expire and not force:
            return None

    price = None
    try:
        t = yf.Ticker(lookup_ticker)
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
            df = yf.download(lookup_ticker, period="1d", progress=False, multi_level_index=False)
            if not df.empty and "Close" in df.columns:
                price = float(df["Close"].iloc[-1])
    except Exception as exc:
        logger.warning(f"Error fetching live market price for ticker {lookup_ticker}: {exc}")

    if price is not None and price > 0:
        price = round(float(price), 4)
        set_cached_price(clean_ticker, price)
        if lookup_ticker != clean_ticker:
            set_cached_price(lookup_ticker, price)
        return price
    else:
        with _CACHE_LOCK:
            _NEGATIVE_CACHE[clean_ticker] = time.time() + 120.0
            if lookup_ticker != clean_ticker:
                _NEGATIVE_CACHE[lookup_ticker] = time.time() + 120.0

    return None


def fetch_latest_prices(tickers: List[str], max_workers: int = 6, force: bool = False) -> Dict[str, float]:
    """Concurrently fetch latest market prices for a list of tickers (stocks and crypto)."""
    cleaned = list({t.strip().upper() for t in tickers if t and t.strip()})
    if not cleaned:
        return {}

    results: Dict[str, float] = {}
    missing: List[str] = []

    # Check cache first (unless forced)
    if not force:
        for ticker in cleaned:
            lookup = normalize_crypto_ticker(ticker)
            cached = get_cached_price(ticker) or get_cached_price(lookup)
            if cached is not None:
                results[ticker] = cached
            else:
                missing.append(ticker)
    else:
        missing = list(cleaned)

    if not missing:
        return results

    # Fetch remaining missing tickers concurrently
    workers = min(len(missing), max_workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_single_ticker_price, ticker, DEFAULT_CACHE_TTL, force): ticker for ticker in missing}
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
    If so, fetches dynamic latest prices and updates the database.
    Falls back gracefully to entry price if market quote is unavailable.
    Returns a dictionary of ticker -> price.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if force:
            cur.execute("""
                SELECT id, ticker, buy_price, last_price, last_price_updated_at
                FROM holdings
                WHERE portfolio_id = %s
            """, (portfolio_id,))
        else:
            cur.execute("""
                SELECT id, ticker, buy_price, last_price, last_price_updated_at
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
    new_prices = fetch_latest_prices(tickers, force=force)

    try:
        with conn.cursor() as cur:
            for row in stale_holdings:
                ticker = row["ticker"]
                lookup_t = normalize_crypto_ticker(ticker)
                price = (
                    new_prices.get(ticker) or
                    new_prices.get(lookup_t) or
                    get_cached_price(ticker) or
                    get_cached_price(lookup_t)
                )
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
                elif row.get("last_price") is None and row.get("buy_price") is not None:
                    # Interim fallback to entry price if market quote is temporarily offline
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["buy_price"], row["id"]))
                    new_prices[ticker] = float(row["buy_price"])
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
            SELECT h.id, h.ticker, h.buy_price, h.last_price, h.last_price_updated_at
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

    try:
        with conn.cursor() as cur:
            for row in stale_holdings:
                ticker = row["ticker"]
                lookup_t = normalize_crypto_ticker(ticker)
                price = (
                    new_prices.get(ticker) or
                    new_prices.get(lookup_t) or
                    get_cached_price(ticker) or
                    get_cached_price(lookup_t)
                )
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
                elif row.get("last_price") is None and row.get("buy_price") is not None:
                    # Interim fallback to entry price if market quote is temporarily offline
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["buy_price"], row["id"]))
                    new_prices[ticker] = float(row["buy_price"])
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
                    cur.execute("SELECT id, ticker, buy_price, last_price FROM holdings WHERE portfolio_id = %s", (portfolio_id,))
                else:
                    cur.execute("""
                        SELECT id, ticker, buy_price, last_price FROM holdings
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

        # Phase 2: External network fetch (ZERO DB connections held during external requests)
        tickers = list({row["ticker"] for row in stale_holdings})
        new_prices = fetch_latest_prices(tickers, force=force)

        # Phase 3: Check out DB connection briefly to apply updates and commit (<1ms)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for row in stale_holdings:
                    ticker = row["ticker"]
                    lookup_t = normalize_crypto_ticker(ticker)
                    price = (
                        new_prices.get(ticker) or
                        new_prices.get(lookup_t) or
                        get_cached_price(ticker) or
                        get_cached_price(lookup_t)
                    )
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
                    elif row.get("last_price") is None and row.get("buy_price") is not None:
                        # Interim fallback to entry price if market quote is temporarily offline
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (row["buy_price"], row["id"]))
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
                    SELECT h.id, h.ticker, h.buy_price, h.last_price FROM holdings h
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

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for row in stale_holdings:
                    ticker = row["ticker"]
                    lookup_t = normalize_crypto_ticker(ticker)
                    price = (
                        new_prices.get(ticker) or
                        new_prices.get(lookup_t) or
                        get_cached_price(ticker) or
                        get_cached_price(lookup_t)
                    )
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
                    elif row.get("last_price") is None and row.get("buy_price") is not None:
                        # Interim fallback to entry price if market quote is temporarily offline
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (row["buy_price"], row["id"]))
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



