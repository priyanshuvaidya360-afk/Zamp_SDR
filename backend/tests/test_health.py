from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_seller_profile_is_exposed_read_only():
    body = TestClient(app).get("/seller").json()
    assert body["company"] == "Halyard" and "offer" in body and "competitors" not in body


def test_cors_allows_the_deployed_frontend_by_pattern(monkeypatch):
    # render.yaml's pattern: the Vercel project's production and preview URLs, nothing else.
    import importlib

    from fastapi.testclient import TestClient

    from app import main
    from app.config import settings
    monkeypatch.setattr(settings, "cors_origin_regex", r"https://zamp-sdr(-[a-z0-9-]+)?\.vercel\.app")
    app = importlib.reload(main).app
    client = TestClient(app)  # no `with`: the startup (database work) is not needed for CORS

    def allowed(origin):
        r = client.options("/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        return r.headers.get("access-control-allow-origin") == origin

    assert allowed("https://zamp-sdr.vercel.app")
    assert allowed("https://zamp-sdr-git-main-priyanshuvaidya.vercel.app")
    assert allowed("http://localhost:3000")
    assert not allowed("https://evil.vercel.app")
    assert not allowed("https://zamp-sdr.vercel.app.evil.com")
    monkeypatch.undo()
    importlib.reload(main)
