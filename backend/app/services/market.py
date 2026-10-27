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
DEFAULT_NGX_CACHE_TTL = 900.0  # 15 minutes for Nigerian Exchange (quotes update every 20-30m)
_KOBO_CIRCUIT_BREAKER_UNTIL = 0.0  # epoch timestamp until which Kobo API calls are blocked (e.g. on 429)

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


def get_seconds_until_midnight_wat() -> float:
    """Compute seconds remaining until midnight West Africa Time (WAT / UTC+1) when Kobo quota resets."""
    from datetime import datetime, timezone, timedelta
    wat_tz = timezone(timedelta(hours=1))
    now_wat = datetime.now(wat_tz)
    tomorrow_wat = (now_wat + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)
    diff = (tomorrow_wat - now_wat).total_seconds()
    return max(diff, 1800.0)


def is_kobo_circuit_breaker_active() -> bool:
    """Check if Kobo Terminal API is currently in cooldown (e.g. after receiving 429)."""
    global _KOBO_CIRCUIT_BREAKER_UNTIL
    now = time.time()
    if now < _KOBO_CIRCUIT_BREAKER_UNTIL:
        return True
    try:
        from app.services.cache import cache
        cached_until = cache.get("kobo_circuit_breaker_until")
        if cached_until and now < float(cached_until):
            _KOBO_CIRCUIT_BREAKER_UNTIL = float(cached_until)
            return True
    except Exception:
        pass
    return False


def trip_kobo_circuit_breaker(duration_seconds: float = 900.0, reason: str = "") -> None:
    """Trip circuit breaker to prevent external calls when rate limits/quotas are exceeded."""
    global _KOBO_CIRCUIT_BREAKER_UNTIL
    _KOBO_CIRCUIT_BREAKER_UNTIL = time.time() + duration_seconds
    try:
        from app.services.cache import cache
        cache.set("kobo_circuit_breaker_until", _KOBO_CIRCUIT_BREAKER_UNTIL, ttl=int(duration_seconds))
        if reason:
            cache.set("kobo_circuit_breaker_reason", reason, ttl=int(duration_seconds))
    except Exception:
        pass
    logger.warning(
        f"[Kobo Terminal] Circuit breaker activated for {int(duration_seconds)}s "
        f"({reason or 'Rate limit/Quota exceeded'}). "
        f"API calls suspended until {time.strftime('%H:%M:%S', time.localtime(_KOBO_CIRCUIT_BREAKER_UNTIL))}"
    )


def get_cached_price(ticker: str, ttl: Optional[float] = None) -> Optional[float]:
    ticker = ticker.strip().upper()
    if ttl is None:
        ttl = DEFAULT_NGX_CACHE_TTL if is_ngx_ticker(ticker) else DEFAULT_CACHE_TTL
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


def fetch_kobo_all_stocks(ttl: float = DEFAULT_NGX_CACHE_TTL, force: bool = False) -> Dict[str, float]:
    """
    Fetch market prices for all 150+ NGX listed equities in a single request
    via GET /api/ngxdata/stocks. Populates cache for all tickers simultaneously,
    saving requests against daily quota.
    """
    if is_kobo_circuit_breaker_active():
        logger.debug("[Kobo Terminal] Circuit breaker active, skipping bulk stock fetch")
        return {}

    if not force:
        bulk_cached = get_cached_price("__NGX_ALL_STOCKS_SYNCED__", ttl=ttl)
        if bulk_cached is not None:
            # Return currently cached NGX tickers
            now = time.time()
            with _CACHE_LOCK:
                return {
                    k: v[0] for k, v in _PRICE_CACHE.items()
                    if is_ngx_ticker(k) and (now - v[1]) <= ttl
                }

    from app.config import Settings
    settings = Settings()
    api_key = settings.effective_kobo_api_key
    base_url = (settings.KOBO_TERMINAL_BASE_URL or "https://koboterminal.com").rstrip("/")

    headers = {
        "Accept": "application/json",
        "User-Agent": "Stock-Sentinel/1.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key

    url = f"{base_url}/api/ngxdata/stocks"
    results: Dict[str, float] = {}

    try:
        import httpx
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                stocks = []
                if isinstance(data, dict):
                    stocks = data.get("stocks") or data.get("data") or []
                elif isinstance(data, list):
                    stocks = data

                if isinstance(stocks, list):
                    for item in stocks:
                        if not isinstance(item, dict):
                            continue
                        sym = item.get("symbol")
                        if not sym:
                            continue
                        clean_sym = sym.strip().upper()
                        # Extract price candidates
                        price_val = (
                            item.get("current_price") or
                            item.get("price") or
                            item.get("close_price") or
                            item.get("close") or
                            item.get("previous_close")
                        )
                        if price_val is not None:
                            try:
                                p = round(float(price_val), 4)
                                if p > 0:
                                    base = normalize_ngx_symbol(clean_sym)
                                    set_cached_price(clean_sym, p)
                                    set_cached_price(base, p)
                                    set_cached_price(f"{base}.NG", p)
                                    set_cached_price(f"{base}.LG", p)
                                    results[clean_sym] = p
                                    results[base] = p
                                    results[f"{base}.NG"] = p
                                    results[f"{base}.LG"] = p
                            except (ValueError, TypeError):
                                pass
                    set_cached_price("__NGX_ALL_STOCKS_SYNCED__", 1.0)
                    logger.info(f"[Kobo Terminal] Successfully refreshed bulk NGX prices ({len(results)} equities)")
                    return results
            elif resp.status_code == 429:
                err_text = resp.text.lower()
                if "daily" in err_text or "limit" in err_text or "midnight" in err_text:
                    cooldown = get_seconds_until_midnight_wat()
                    trip_kobo_circuit_breaker(cooldown, reason="Daily 100-request quota reached (resets at midnight WAT)")
                else:
                    trip_kobo_circuit_breaker(900.0, reason="Rate limit reached (15m cooldown)")
            elif resp.status_code in (401, 403):
                logger.warning(f"[Kobo Terminal] Authentication error {resp.status_code} on bulk stocks fetch")
    except Exception as exc:
        logger.warning(f"[Kobo Terminal] Bulk stocks fetch failed: {exc}")

    return results


def fetch_kobo_ticker_price(ticker: str, ttl: float = DEFAULT_NGX_CACHE_TTL, force: bool = False) -> Optional[float]:
    """
    Fetch live dynamic market price for a Nigerian stock from Kobo Terminal API.
    Shares the same cache, respects circuit breaker, and queries the official endpoint.
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    symbol = normalize_ngx_symbol(clean_ticker)

    # 1. Cache hit (unless forced)
    if not force:
        cached = get_cached_price(clean_ticker, ttl=ttl) or get_cached_price(symbol, ttl=ttl) or get_cached_price(f"{symbol}.NG", ttl=ttl)
        if cached is not None:
            return cached

    # 2. Check circuit breaker and negative cache
    now = time.time()
    with _CACHE_LOCK:
        neg_expire = _NEGATIVE_CACHE.get(clean_ticker)
        if neg_expire and now < neg_expire and not force:
            return None

    if is_kobo_circuit_breaker_active():
        return None

    # 3. Check if bulk sync can fulfill this request (1 call covers all NGX stocks)
    bulk_results = fetch_kobo_all_stocks(ttl=ttl, force=force)
    if symbol in bulk_results:
        return bulk_results[symbol]
    if clean_ticker in bulk_results:
        return bulk_results[clean_ticker]
    if f"{symbol}.NG" in bulk_results:
        return bulk_results[f"{symbol}.NG"]

    # Re-check cache after bulk attempt
    cached = get_cached_price(clean_ticker, ttl=ttl) or get_cached_price(symbol, ttl=ttl)
    if cached is not None and not force:
        return cached

    if is_kobo_circuit_breaker_active():
        return None

    # 4. Fallback: single ticker query to official /api/ngxdata/prices/:symbol?days=1
    from app.config import Settings
    settings = Settings()
    api_key = settings.effective_kobo_api_key
    base_url = (settings.KOBO_TERMINAL_BASE_URL or "https://koboterminal.com").rstrip("/")

    headers = {
        "Accept": "application/json",
        "User-Agent": "Stock-Sentinel/1.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key

    # Note: Free tier requires ?days=1 (or <= 7); omitting days asks for full history since 2000, triggering 403
    url = f"{base_url}/api/ngxdata/prices/{symbol}?days=1"
    price = None

    try:
        import httpx
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    # Check prices array if present
                    prices_arr = data.get("prices")
                    if isinstance(prices_arr, list) and prices_arr:
                        latest_quote = prices_arr[0]
                        if isinstance(latest_quote, dict):
                            for key in ("close_price", "current_price", "price", "open_price", "last"):
                                val = latest_quote.get(key)
                                if val is not None:
                                    try:
                                        cand_val = float(val)
                                        if cand_val > 0:
                                            price = cand_val
                                            break
                                    except (ValueError, TypeError):
                                        continue
                    if price is None:
                        inner = data.get("data") if isinstance(data.get("data"), dict) else data
                        for key in ("current_price", "price", "close_price", "last_price", "close", "last"):
                            val = inner.get(key)
                            if val is not None:
                                try:
                                    cand_val = float(val)
                                    if cand_val > 0:
                                        price = cand_val
                                        break
                                except (ValueError, TypeError):
                                    continue
            elif resp.status_code == 429:
                err_text = resp.text.lower()
                if "daily" in err_text or "limit" in err_text or "midnight" in err_text:
                    cooldown = get_seconds_until_midnight_wat()
                    trip_kobo_circuit_breaker(cooldown, reason="Daily 100-request quota reached (resets at midnight WAT)")
                else:
                    trip_kobo_circuit_breaker(900.0, reason="Rate limit reached (15m cooldown)")
            elif resp.status_code == 404:
                with _CACHE_LOCK:
                    _NEGATIVE_CACHE[clean_ticker] = time.time() + 300.0
    except Exception as exc:
        logger.warning(f"Error fetching Kobo Terminal price for {symbol}: {exc}")

    if price is not None and price > 0:
        price = round(float(price), 4)
        set_cached_price(clean_ticker, price)
        set_cached_price(symbol, price)
        set_cached_price(f"{symbol}.NG", price)
        return price

    return None


def fetch_single_ticker_price(ticker: str, ttl: float = DEFAULT_CACHE_TTL, force: bool = False) -> Optional[float]:
    """
    Fetch current dynamic market price for a single ticker.
    Routes Nigerian stocks (NGN) to Kobo Terminal API and US/global stocks (USD) to YFinance.
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        return None

    if not force:
        cached = get_cached_price(clean_ticker, ttl=ttl)
        if cached is not None:
            return cached

    # Smart Routing: Nigerian Exchange tickers -> Kobo Terminal API
    if is_ngx_ticker(clean_ticker):
        return fetch_kobo_ticker_price(clean_ticker, ttl=ttl, force=force)

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


def fetch_latest_prices(tickers: List[str], max_workers: int = 6, force: bool = False) -> Dict[str, float]:
    """Concurrently fetch latest market prices for a list of tickers."""
    cleaned = list({t.strip().upper() for t in tickers if t and t.strip()})
    if not cleaned:
        return {}

    results: Dict[str, float] = {}
    missing: List[str] = []

    # Check cache first (unless forced)
    if not force:
        for ticker in cleaned:
            cached = get_cached_price(ticker)
            if cached is not None:
                results[ticker] = cached
            else:
                missing.append(ticker)
    else:
        missing = list(cleaned)

    if not missing:
        return results

    # If any NGX tickers are missing, attempt bulk NGX fetch once (populates all NGX symbols)
    ngx_missing = [t for t in missing if is_ngx_ticker(t)]
    if ngx_missing and not is_kobo_circuit_breaker_active():
        bulk_data = fetch_kobo_all_stocks(force=force)
        if bulk_data:
            still_missing = []
            for t in missing:
                base_t = normalize_ngx_symbol(t)
                p = (
                    bulk_data.get(t) or
                    bulk_data.get(base_t) or
                    bulk_data.get(f"{base_t}.NG") or
                    get_cached_price(t)
                )
                if p is not None:
                    results[t] = p
                else:
                    still_missing.append(t)
            missing = still_missing

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
                base_t = normalize_ngx_symbol(ticker)
                price = (
                    new_prices.get(ticker) or
                    new_prices.get(base_t) or
                    new_prices.get(f"{base_t}.NG") or
                    get_cached_price(ticker) or
                    get_cached_price(base_t)
                )
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
                elif row.get("last_price") is None and row.get("buy_price") is not None:
                    # Interim fallback to entry price if market quote is temporarily rate-limited or offline
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["buy_price"], row["id"]))
                    new_prices[ticker] = float(row["buy_price"])
                elif row.get("last_price") is not None and is_ngx_ticker(ticker) and is_kobo_circuit_breaker_active():
                    # Preserve existing last price and bump timestamp during quota cooldown to prevent tight-loop polling
                    cur.execute("""
                        UPDATE holdings
                        SET last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["id"],))
                    new_prices[ticker] = float(row["last_price"])
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
                base_t = normalize_ngx_symbol(ticker)
                price = (
                    new_prices.get(ticker) or
                    new_prices.get(base_t) or
                    new_prices.get(f"{base_t}.NG") or
                    get_cached_price(ticker) or
                    get_cached_price(base_t)
                )
                if price is not None:
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (price, row["id"]))
                elif row.get("last_price") is None and row.get("buy_price") is not None:
                    # Interim fallback to entry price if market quote is temporarily rate-limited or offline
                    cur.execute("""
                        UPDATE holdings
                        SET last_price = %s,
                            last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["buy_price"], row["id"]))
                    new_prices[ticker] = float(row["buy_price"])
                elif row.get("last_price") is not None and is_ngx_ticker(ticker) and is_kobo_circuit_breaker_active():
                    # Preserve existing last price and bump timestamp during quota cooldown to prevent tight-loop polling
                    cur.execute("""
                        UPDATE holdings
                        SET last_price_updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (row["id"],))
                    new_prices[ticker] = float(row["last_price"])
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
                    base_t = normalize_ngx_symbol(ticker)
                    price = (
                        new_prices.get(ticker) or
                        new_prices.get(base_t) or
                        new_prices.get(f"{base_t}.NG") or
                        get_cached_price(ticker) or
                        get_cached_price(base_t)
                    )
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
                    elif row.get("last_price") is None and row.get("buy_price") is not None:
                        # Interim fallback to entry price if market quote is temporarily rate-limited or offline
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (row["buy_price"], row["id"]))
                    elif row.get("last_price") is not None and is_ngx_ticker(ticker) and is_kobo_circuit_breaker_active():
                        # Preserve existing last price and bump timestamp during quota cooldown to prevent tight-loop polling
                        cur.execute("""
                            UPDATE holdings
                            SET last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (row["id"],))
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
                    base_t = normalize_ngx_symbol(ticker)
                    price = (
                        new_prices.get(ticker) or
                        new_prices.get(base_t) or
                        new_prices.get(f"{base_t}.NG") or
                        get_cached_price(ticker) or
                        get_cached_price(base_t)
                    )
                    if price is not None:
                        cur.execute("""
                            UPDATE holdings
                            SET last_price = %s,
                                last_price_updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (price, row["id"]))
                    elif row.get("last_price") is None and row.get("buy_price") is not None:
                        # Interim fallback to entry price if market quote is temporarily rate-limited or offline
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



