from fastapi.testclient import TestClient

from app.api.main import app


def test_health_creates_database(tmp_path, monkeypatch):
    db = tmp_path / "secretary.db"
    monkeypatch.setenv("SECRETARY_DB_PATH", str(db))
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert db.exists()
