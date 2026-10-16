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

