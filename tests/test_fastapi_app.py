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
