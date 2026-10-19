from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_seller_profile_is_exposed_read_only():
    body = TestClient(app).get("/seller").json()
    assert body["company"] == "Halyard" and "offer" in body and "competitors" not in body
