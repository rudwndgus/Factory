import json
import time
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from factory import database as db, engine, reports, security, youtube
from factory.providers import reserve, BudgetBlocked, similarity
from factory.main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "test.sqlite3")
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OWNER_PASSWORD", "test-owner-password-123456")
    db.migrate()


def first():
    return db.offices()[0]["id"]


def job(id):
    with db.connection() as c:
        return dict(c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone())


def test_state_persists_and_pause_blocks_new_stages(monkeypatch):
    id = first()
    j = engine.enqueue(id, True)
    engine.set_mode(id, "PAUSED")
    monkeypatch.setattr(
        engine, "run_stage", lambda _: pytest.fail("Paused work must not run")
    )
    engine.tick()
    assert job(j)["status"] == "QUEUED"
    db.migrate()
    assert db.office(id)["mode"] == "PAUSED"


def test_emergency_cancels_and_blocks_new_jobs():
    id = first()
    j = engine.enqueue(id, True)
    engine.set_mode(id, "EMERGENCY_STOP")
    assert job(j)["status"] == "CANCELLED"
    with pytest.raises(ValueError):
        engine.enqueue(id, True)


def test_recovery_keeps_stage_checkpoint():
    j = engine.enqueue(first(), True)
    with db.connection() as c:
        c.execute("UPDATE jobs SET stage=4,status='RUNNING' WHERE id=?", (j,))
    engine.recover()
    assert job(j)["stage"] == 4
    assert job(j)["status"] == "QUEUED"


def test_retry_backoff_and_manual_retry(monkeypatch):
    j = engine.enqueue(first(), True)

    def fail(_):
        raise RuntimeError("provider secret must not be persisted")

    monkeypatch.setattr(engine, "run_stage", fail)
    engine.tick()
    assert job(j)["status"] == "RETRYING"
    assert job(j)["next_run"] > time.time()
    assert "secret" not in job(j)["error"]
    with db.connection() as c:
        c.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (j,))
    engine.retry(first(), j)
    assert job(j)["stage"] == 0
    assert job(j)["status"] == "QUEUED"


def test_budget_hard_stop():
    id = first()
    reserve(id, "v", "script", 2.9)
    with pytest.raises(BudgetBlocked):
        reserve(id, "v", "script", 0.2)
    assert len(db.rows("cost_events", id)) == 1


def test_global_budget(monkeypatch):
    monkeypatch.setenv("GLOBAL_DAILY_BUDGET", "0.10")
    reserve(first(), "v", "script", 0.08)
    other = db.create_office("Two", {})["id"]
    with pytest.raises(BudgetBlocked):
        reserve(other, "v", "script", 0.08)


def test_duplicate_detection():
    assert similarity("Why is SPACE silent?", "why is space silent") > 0.95


def test_office_isolation():
    a = first()
    b = db.create_office("Second", {})["id"]
    db.put("videos", a, {"title": "private project"})
    assert not db.rows("videos", b)
    assert len(db.rows("videos", a)) == 1


def test_secret_roundtrip():
    security.save_secret("OPENAI_API_KEY", "not-a-real-key")
    assert security.secret("OPENAI_API_KEY") == "not-a-real-key"
    with db.connection() as c:
        assert (
            "not-a-real-key"
            not in c.execute("SELECT encrypted FROM integrations").fetchone()[0]
        )


def test_report_has_no_fake_metrics():
    reports.report(first())
    r = db.rows("reports", first())[0]["data"]
    assert r["produced"] == 0
    assert r["analytics"] == {}
    assert "Insufficient" in r["recommendation"]


def client():
    c = TestClient(app)
    token = c.post(
        "/api/auth/login", json={"password": "test-owner-password-123456"}
    ).json()["token"]
    c.headers["Authorization"] = "Bearer " + token
    return c


def test_qc_and_test_publication_blocked(monkeypatch):
    id = first()
    v = "v"
    db.put(
        "videos",
        id,
        {"id": v, "test_mode": True, "qc": {"audio": True}, "status": "APPROVED"},
        v,
        v,
    )

    def forbidden(*args):
        pytest.fail("Upload must not be called")

    monkeypatch.setattr(youtube.OfficialYouTube, "upload", forbidden)
    assert (
        client()
        .post(f"/api/offices/{id}/videos/{v}/publish", json={"confirmed": True})
        .status_code
        == 400
    )


def test_mocked_upload_gate(monkeypatch):
    id = first()
    engine.set_mode(id, "RUNNING")
    v = "v"
    db.put(
        "videos",
        id,
        {
            "id": v,
            "title": "real",
            "test_mode": False,
            "qc": {"audio": True, "facts": True},
            "status": "APPROVED",
            "file": "test.mp4",
        },
        v,
        v,
    )
    monkeypatch.setattr(
        youtube.OfficialYouTube, "upload", lambda *a: {"youtube_id": "mock-id"}
    )
    response = client().post(
        f"/api/offices/{id}/videos/{v}/publish", json={"confirmed": True}
    )
    assert response.status_code == 200
    assert response.json()["youtube_id"] == "mock-id"


def test_mocked_analytics(monkeypatch):
    monkeypatch.setattr(
        youtube.OfficialYouTube, "analytics", lambda s: [{"viewCount": 12}]
    )
    assert client().post(f"/api/offices/{first()}/analytics/refresh").json() == [
        {"viewCount": 12}
    ]


def test_auth_and_oauth_state():
    c = TestClient(app)
    assert c.get("/api/offices").status_code == 401
    with pytest.raises(ValueError):
        youtube.callback("fake", "wrong-state")


def test_missing_provider_blocks_not_completes(monkeypatch):
    from factory.providers import ConfigurationRequired

    def blocked(_):
        raise ConfigurationRequired("Configure provider")

    monkeypatch.setattr(engine, "run_stage", blocked)
    j = engine.enqueue(first(), True)
    engine.tick()
    assert job(j)["status"] == "BLOCKED"


def test_office_api_rejects_foreign_video():
    a = first()
    b = db.create_office("B", {})["id"]
    db.put("videos", a, {"title": "A"}, "v", "v")
    assert client().get(f"/api/offices/{b}/videos/v").status_code == 404
