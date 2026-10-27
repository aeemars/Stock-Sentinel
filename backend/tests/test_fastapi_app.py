from fastapi.testclient import TestClient
from main import app
from app.session import dump_session, load_session
from app.auth import User, hash_password, check_password

client = TestClient(app, follow_redirects=False)

def test_routes_exist():
    # Public routes
    resp_login = client.get("/login")
    assert resp_login.status_code == 200
    assert "Sign in" in resp_login.text

    resp_reg = client.get("/register")
    assert resp_reg.status_code == 200
    assert "Create" in resp_reg.text

def test_root_redirects_to_dashboard():
    resp = client.get("/")
    assert resp.status_code == 303
    assert "/dashboard" in resp.headers.get("location", "")

def test_protected_routes_redirect_to_login():
    for path in ["/dashboard", "/alerts", "/analytics", "/settings", "/support", "/portfolio/1"]:
        resp = client.get(path)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

def test_csrf_protection_on_post():
    # Submitting POST to /login without valid csrf_token should fail CSRF check
    resp = client.post("/login", data={"identifier": "test", "password": "pass"})
    assert resp.status_code == 303
    # Check that session cookie is set with flash message
    assert "session" in resp.headers.get("set-cookie", "")

def test_password_hashing():
    pw = "secretpassword123"
    hashed = hash_password(pw)
    assert hashed != pw
    assert check_password(pw, hashed) is True
    assert check_password("wrongpassword", hashed) is False

def test_session_serialization():
    data = {"user_id": 42, "_csrf_token": "token123"}
    token = dump_session(data)
    assert isinstance(token, str)
    unpacked = load_session(token)
    assert unpacked == data

def test_api_workflow_auth():
    resp = client.post("/api/run-workflow")
    assert resp.status_code in [401, 503]

def test_weighted_average_buy_price_logic():
    # Scenario 1: Initial buy 10 shares @ $100
    shares_1 = 10.0
    price_1 = 100.0
    
    # Additional buy: 10 shares @ $120
    shares_2 = 10.0
    price_2 = 120.0
    
    total_shares = shares_1 + shares_2
    weighted_avg = round(((shares_1 * price_1) + (shares_2 * price_2)) / total_shares, 4)
    assert weighted_avg == 110.0

    # Scenario 2: 15 shares @ $150, then 5 shares @ $200
    s_a, p_a = 15.0, 150.0
    s_b, p_b = 5.0, 200.0
    total_s = s_a + s_b
    weighted_avg_2 = round(((s_a * p_a) + (s_b * p_b)) / total_s, 4)
    assert weighted_avg_2 == 162.5

def test_pnl_calculation_logic():
    shares = 10.0
    buy_price = 100.0
    current_market_price = 125.50

    cost_basis = round(shares * buy_price, 2)
    market_value = round(shares * current_market_price, 2)
    pnl = round(market_value - cost_basis, 2)
    pnl_pct = round((pnl / cost_basis) * 100, 2)

    assert cost_basis == 1000.0
    assert market_value == 1255.0
    assert pnl == 255.0
    assert pnl_pct == 25.50

def test_market_service_caching_and_metrics():
    from app.services.market import (
        set_cached_price,
        get_cached_price,
        fetch_single_ticker_price,
        calculate_holding_metrics,
    )

    # In-memory cache test
    set_cached_price("MOCKCO", 99.75)
    assert get_cached_price("MOCKCO") == 99.75
    # fetch_single_ticker_price should return cached price without network call
    assert fetch_single_ticker_price("MOCKCO") == 99.75

    # Holding metrics with gain
    metrics_gain = calculate_holding_metrics({"shares": 5.0, "buy_price": 50.0, "last_price": 75.0})
    assert metrics_gain["cost_basis"] == 250.0
    assert metrics_gain["market_value"] == 375.0
    assert metrics_gain["pnl"] == 125.0
    assert metrics_gain["pnl_pct"] == 50.0

    # Holding metrics with loss
    metrics_loss = calculate_holding_metrics({"shares": 10.0, "buy_price": 100.0, "last_price": 80.0})
    assert metrics_loss["cost_basis"] == 1000.0
    assert metrics_loss["market_value"] == 800.0
    assert metrics_loss["pnl"] == -200.0
    assert metrics_loss["pnl_pct"] == -20.0

    # Holding metrics with missing buy price
    metrics_no_bp = calculate_holding_metrics({"shares": 10.0, "buy_price": None, "last_price": 150.0})
    assert metrics_no_bp["cost_basis"] == 0.0
    assert metrics_no_bp["market_value"] == 1500.0
    assert metrics_no_bp["pnl"] is None

def test_live_prices_unauthenticated():
    resp = client.get("/portfolio/1/live-prices")
    assert resp.status_code == 303
    assert "/login" in resp.headers.get("location", "")

def test_support_page_loads():
    # Unauthenticated should redirect
    resp_unauth = client.get("/support")
    assert resp_unauth.status_code == 303
    assert "/login" in resp_unauth.headers.get("location", "")

    # Authenticated user should load support page
    from app.services.cache import set_cached_user
    mock_user = User({"id": 777, "username": "alex", "email": "alex@example.com"})
    set_cached_user(mock_user.id, mock_user)
    session_cookie = dump_session({"user_id": 777, "_csrf_token": "token777"})

    client.cookies.set("session", session_cookie)
    resp = client.get("/support")
    assert resp.status_code == 200
    assert "Contact Support" in resp.text
    assert "https://github.com/aeemars/Stock-Sentinel" in resp.text
    assert "Direct Inbox" in resp.text
    client.cookies.clear()

def test_support_contact_endpoint():
    from app.services.cache import set_cached_user
    mock_user = User({"id": 888, "username": "sarah", "email": "sarah@example.com"})
    set_cached_user(mock_user.id, mock_user)
    session_token = dump_session({"user_id": 888, "_csrf_token": "csrf_sarah"})

    # 1. Validation error: missing subject
    client.cookies.set("session", session_token)
    resp_missing_sub = client.post(
        "/support/contact",
        data={"csrf_token": "csrf_sarah", "subject": "", "message": "Hello test message"},
        headers={"accept": "application/json"},
    )
    assert resp_missing_sub.status_code == 422
    assert resp_missing_sub.json()["status"] == "error"

    # 2. Validation error: missing message
    resp_missing_msg = client.post(
        "/support/contact",
        data={"csrf_token": "csrf_sarah", "subject": "Valid subject", "message": ""},
        headers={"accept": "application/json"},
    )
    assert resp_missing_msg.status_code == 422

    # 3. Successful AJAX submission
    resp_success = client.post(
        "/support/contact",
        data={
            "csrf_token": "csrf_sarah",
            "category": "bug",
            "subject": "Chart rendering latency issue",
            "message": "When switching tickers quickly, chart takes 2 seconds to reload.",
        },
        headers={"accept": "application/json"},
    )
    assert resp_success.status_code == 200
    data = resp_success.json()
    assert data["status"] == "success"
    assert "The support team will get back to you shortly" in data["message"]

    client.cookies.clear()

def test_email_simulation_fallback(monkeypatch):
    from app.config import settings
    from app.services.email import (
        build_support_email_html,
        send_support_email,
    )

    # Email HTML builder
    email_html = build_support_email_html(
        username="testdev",
        user_email="testdev@example.com",
        category="bug",
        subject="Test Bug Subject",
        message="Steps to reproduce: ...",
        timestamp_str="2026-10-18 12:00:00",
    )
    assert "testdev" in email_html
    assert "testdev@example.com" in email_html
    assert "Test Bug Subject" in email_html
    assert "Bug" in email_html

    # Mock dispatch (no SMTP set)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)
    result = send_support_email(
        username="testdev",
        user_email="testdev@example.com",
        category="question",
        subject="How does the AI workflow work?",
        message="Tell me more about the automated AI market intelligence pipeline",
    )
    assert result["status"] == "success"
    assert result.get("simulated") is True


def test_smtp_email_dispatch(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import settings
    from app.services.email import send_support_email

    # Mock SMTP settings
    monkeypatch.setattr(settings, "SMTP_USER", "tester@gmail.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app_pass_1234")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "DEV_NOTIFICATION_EMAIL", "dev@example.com")

    # Mock smtplib.SMTP
    mock_smtp_instance = MagicMock()
    mock_smtp_class = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__enter__.return_value = mock_smtp_instance

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp_class)

    result = send_support_email(
        username="john_doe",
        user_email="john@example.com",
        category="bug",
        subject="Login button misaligned",
        message="On mobile, the login button overlaps with the footer.",
    )

    assert result["status"] == "success"
    assert result["provider"] == "smtp"
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("tester@gmail.com", "app_pass_1234")
    mock_smtp_instance.send_message.assert_called_once()


def test_ai_simulation_fallback(monkeypatch):
    from app.services.ai import analyze_holdings, format_analysis_as_alert, _simulate_analysis
    from app.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    holdings_data = [
        {"ticker": "NVDA", "shares": 10.0, "buy_price": 100.0, "last_price": 135.0, "pnl_pct": 35.0},
        {"ticker": "AAPL", "shares": 15.0, "buy_price": 150.0, "last_price": 160.0, "pnl_pct": 6.67},
        {"ticker": "MSFT", "shares": 8.0, "buy_price": 400.0, "last_price": 395.0, "pnl_pct": -1.25},
        {"ticker": "TSLA", "shares": 20.0, "buy_price": 250.0, "last_price": 220.0, "pnl_pct": -12.0},
        {"ticker": "INTC", "shares": 50.0, "buy_price": 40.0, "last_price": 22.0, "pnl_pct": -45.0},
    ]

    results = analyze_holdings(holdings_data)
    assert len(results) == 5

    res_by_ticker = {r["ticker"]: r for r in results}
    assert res_by_ticker["NVDA"]["signal"] == "TAKE_PROFIT"
    assert res_by_ticker["AAPL"]["signal"] == "BULLISH"
    assert res_by_ticker["MSFT"]["signal"] == "NEUTRAL"
    assert res_by_ticker["TSLA"]["signal"] == "BEARISH"
    assert res_by_ticker["INTC"]["signal"] == "RISK_ALERT"

    # Test format alert
    alert_text = format_analysis_as_alert("NVDA", res_by_ticker["NVDA"])
    assert "NVDA" in alert_text
    assert "TAKE_PROFIT" in alert_text


def test_ai_openrouter_mock_api(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import settings
    from app.services.ai import analyze_holdings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-fake-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '[{"ticker": "AAPL", "signal": "BULLISH", "risk_level": "LOW", "summary": "Strong momentum.", "reasoning": "• High earnings growth\\n• Stable cash flow"}]'
                }
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp

    import httpx
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    holdings_data = [{"ticker": "AAPL", "shares": 10.0, "buy_price": 150.0, "last_price": 180.0, "pnl_pct": 20.0}]
    results = analyze_holdings(holdings_data)

    assert len(results) == 1
    assert results[0]["ticker"] == "AAPL"
    assert results[0]["signal"] == "BULLISH"
    assert "Strong momentum" in results[0]["summary"]


def test_on_demand_ai_endpoints_unauth():
    # Without CSRF token, verify_csrf redirects to dashboard/safe destination
    resp1 = client.post("/portfolio/1/analyze-ai")
    assert resp1.status_code == 303
    assert "/dashboard" in resp1.headers.get("location", "")

    resp2 = client.post("/alerts/scan-ai")
    assert resp2.status_code == 303
    assert "/dashboard" in resp2.headers.get("location", "")

    # With CSRF token but without authenticated user, get_current_user redirects to /login
    session_cookie = dump_session({"_csrf_token": "csrf123"})
    client.cookies.set("session", session_cookie)
    resp3 = client.post("/portfolio/1/analyze-ai", data={"csrf_token": "csrf123"})
    assert resp3.status_code == 303
    assert "/login" in resp3.headers.get("location", "")
    client.cookies.clear()


def test_security_redirect_validation():
    from app.dependencies import is_safe_redirect
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"host", b"testserver")]}
    req = Request(scope)

    # Malicious inputs must be rejected (SEC-01)
    assert is_safe_redirect(req, "javascript:alert(1)") is False
    assert is_safe_redirect(req, "data:text/html,<script>alert(1)</script>") is False
    assert is_safe_redirect(req, "vbscript:msgbox(1)") is False
    assert is_safe_redirect(req, "/\\attacker.com") is False
    assert is_safe_redirect(req, "//attacker.com") is False
    assert is_safe_redirect(req, "\\\\attacker.com") is False
    assert is_safe_redirect(req, "https://evil.com/phish") is False
    assert is_safe_redirect(req, None) is False
    assert is_safe_redirect(req, "") is False

    # Safe local targets must be allowed
    assert is_safe_redirect(req, "/dashboard") is True
    assert is_safe_redirect(req, "/portfolio/1") is True
    assert is_safe_redirect(req, "http://testserver/dashboard") is True


def test_security_response_headers():
    resp = client.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_security_rate_limiting():
    from app.services.cache import check_rate_limit, record_rate_limit, reset_rate_limit
    test_key = "test_rate_limiter_key"
    reset_rate_limit(test_key)

    assert check_rate_limit(test_key, max_attempts=3) is True
    assert record_rate_limit(test_key, 60) == 1
    assert record_rate_limit(test_key, 60) == 2
    assert check_rate_limit(test_key, max_attempts=3) is True
    assert record_rate_limit(test_key, 60) == 3
    assert check_rate_limit(test_key, max_attempts=3) is False
    reset_rate_limit(test_key)
    assert check_rate_limit(test_key, max_attempts=3) is True


def test_security_ticker_regex_validation():
    from app.routers.holdings import TICKER_REGEX
    assert TICKER_REGEX.match("AAPL") is not None
    assert TICKER_REGEX.match("BRK.B") is not None
    assert TICKER_REGEX.match("BTC-USD") is not None
    assert TICKER_REGEX.match("NVDA") is not None

    # Malicious or invalid symbols (SEC-05)
    assert TICKER_REGEX.match("AAPL; DROP TABLE") is None
    assert TICKER_REGEX.match("AAPL<script>") is None
    assert TICKER_REGEX.match("AAPL\nIGNORE") is None
    assert TICKER_REGEX.match("VERYLONGTICKERNAMEEXCEEDING10") is None
    assert TICKER_REGEX.match("") is None


def test_pwa_manifest_endpoint():
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert "application/manifest+json" in resp.headers.get("content-type", "")
    manifest = resp.json()
    assert manifest["short_name"] == "Stock Sentinel"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/dashboard"
    assert len(manifest["icons"]) >= 4


def test_pwa_service_worker_endpoint():
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "")
    assert resp.headers.get("service-worker-allowed") == "/"
    assert "CACHE_NAME" in resp.text
    assert "stock-sentinel" in resp.text


def test_pwa_offline_fallback_page():
    resp = client.get("/offline")
    assert resp.status_code == 200
    assert "You're Offline" in resp.text
    assert "Stock Sentinel" in resp.text


def test_pwa_meta_tags_in_templates():
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'rel="manifest" href="/manifest.webmanifest"' in resp.text
    assert 'name="apple-mobile-web-app-capable" content="yes"' in resp.text
    assert 'name="theme-color" content="#10131a"' in resp.text
    assert 'sw.js' in resp.text


def test_cloudflare_environment_settings():
    from app.config import Settings
    s_default = Settings(ENVIRONMENT="local_test", VERCEL=None, CLOUDFLARE=None)
    assert s_default.is_serverless is False

    s_cf1 = Settings(ENVIRONMENT="cloudflare")
    assert s_cf1.is_serverless is True
    assert s_cf1.is_vercel is True

    s_cf2 = Settings(CLOUDFLARE="1")
    assert s_cf2.is_serverless is True


def test_cloudflare_https_cookie_detection():
    from app.config import settings
    orig_secure = settings.SESSION_COOKIE_SECURE
    orig_env = settings.ENVIRONMENT
    client.cookies.clear()
    try:
        settings.SESSION_COOKIE_SECURE = None
        settings.ENVIRONMENT = "production"
        resp = client.get("/login", headers={"cf-visitor": '{"scheme":"https"}'})
        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "Secure" in cookie_header or "secure" in cookie_header
    finally:
        client.cookies.clear()
        settings.SESSION_COOKIE_SECURE = orig_secure
        settings.ENVIRONMENT = orig_env


def test_is_ngx_ticker_and_currency_resolution():
    from app.services.market import (
        is_ngx_ticker,
        get_ticker_currency,
        normalize_ngx_symbol,
    )
    assert is_ngx_ticker("DANGCEM") is True
    assert is_ngx_ticker("MTNN.NG") is True
    assert is_ngx_ticker("ZENITHBANK.LG") is True
    assert is_ngx_ticker("NGX:GTCO") is True
    assert is_ngx_ticker("AAPL") is False
    assert is_ngx_ticker("TSLA") is False

    assert get_ticker_currency("DANGCEM") == "NGN"
    assert get_ticker_currency("DANGCEM.NG") == "NGN"
    assert get_ticker_currency("AAPL") == "USD"

    assert normalize_ngx_symbol("DANGCEM.NG") == "DANGCEM"
    assert normalize_ngx_symbol("NGX:MTNN") == "MTNN"
    assert normalize_ngx_symbol("ZENITHBANK.LG") == "ZENITHBANK"


def test_fetch_kobo_ticker_price_mock(monkeypatch):
    import httpx
    from unittest.mock import MagicMock
    from app.services.market import fetch_kobo_ticker_price, get_cached_price

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Official Kobo Terminal JSON payload structure
    mock_resp.json.return_value = {
        "symbol": "DANGCEM",
        "name": "Dangote Cement Plc",
        "current_price": 665.00,
        "change_percent": 2.31,
        "volume": 1245890
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))
    monkeypatch.setenv("X-API-KEY", "kobo_test_key_123")

    price = fetch_kobo_ticker_price("DANGCEM.NG", ttl=0)
    assert price == 665.00
    assert get_cached_price("DANGCEM.NG") == 665.00
    assert get_cached_price("DANGCEM") == 665.00

    # Verify official endpoint was called with X-API-Key header
    called_url = mock_client.get.call_args[0][0]
    called_headers = mock_client.get.call_args[1]["headers"]
    assert "/api/ngxdata/prices/DANGCEM" in called_url
    assert called_headers["X-API-Key"] == "kobo_test_key_123"


def test_fetch_single_ticker_price_routing(monkeypatch):
    from unittest.mock import MagicMock
    import app.services.market as market_mod

    kobo_mock = MagicMock(return_value=290.0)
    monkeypatch.setattr(market_mod, "fetch_kobo_ticker_price", kobo_mock)

    # Calling NGX ticker should invoke Kobo Terminal provider
    p_ngx = market_mod.fetch_single_ticker_price("MTNN.NG", ttl=0)
    assert p_ngx == 290.0
    kobo_mock.assert_called_once()


def test_x_api_key_settings_resolution(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("X-API-KEY", "secret_kobo_key_456")
    s = Settings()
    assert s.effective_kobo_api_key == "secret_kobo_key_456"


def test_fetch_kobo_all_stocks_bulk_payload_mock(monkeypatch):
    import httpx
    from unittest.mock import MagicMock
    from app.services.market import fetch_kobo_all_stocks, get_cached_price

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "stocks": [
            {"symbol": "SEPLAT", "current_price": 16000.10},
            {"symbol": "GTCO", "current_price": 125.10},
            {"symbol": "DANGCEM", "close_price": 1222.10}
        ]
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))
    results = fetch_kobo_all_stocks(ttl=0, force=True)

    assert results["SEPLAT"] == 16000.10
    assert results["GTCO"] == 125.10
    assert results["DANGCEM"] == 1222.10
    assert get_cached_price("SEPLAT") == 16000.10
    assert get_cached_price("GTCO.NG") == 125.10


def test_fetch_kobo_ticker_price_with_days_param(monkeypatch):
    import httpx
    from unittest.mock import MagicMock
    from app.services.market import fetch_kobo_ticker_price

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "symbol": "SEPLAT",
        "prices": [
            {"close_price": 16000.10, "open_price": 16000.10}
        ]
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))
    price = fetch_kobo_ticker_price("SEPLAT", ttl=0, force=True)

    assert price == 16000.10
    called_url = mock_client.get.call_args[0][0]
    assert "?days=1" in called_url





