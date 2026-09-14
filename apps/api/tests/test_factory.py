import json
import time
import pytest
import httpx
import logging
from urllib.parse import urlparse, parse_qs
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


def test_visual_modes_and_provenance(monkeypatch, tmp_path):
    from factory import assets
    settings = db.office(first())["settings"]
    assert settings["visual_source_mode"] == "AI First"
    scene = {"narration": "Space is silent"} | assets.visual_plan("Space is silent", "Space", settings, 0, space_test=True)
    calls = []
    monkeypatch.setattr(assets.OpenAI, "image", lambda self, prompt, path: calls.append("ai") or {"model": "mock"})
    monkeypatch.setattr(assets, "acquire", lambda *a, **kw: calls.append("real") or {"provider": "mock-real"})
    asset = assets.acquire_scene(scene, tmp_path / "a.png", 0, settings, first(), "v")
    assert calls == ["ai"] and asset["asset_source"] == "ai"
    assert asset["image_prompt"] and asset["visual_style"]
    for mode, index in [("Real First", 0), ("Mixed", 1)]:
        calls.clear()
        asset = assets.acquire_scene(scene, tmp_path / "a.png", index, settings | {"visual_source_mode": mode}, first(), "v")
        assert calls == ["real"] and asset["asset_source"] == "external"


def test_documentary_never_fabricated(monkeypatch, tmp_path):
    from factory import assets
    from factory.providers import ConfigurationRequired
    settings = db.office(first())["settings"]
    scene = {"narration": "Official photo"} | assets.visual_plan("Official photo", "NASA", settings, 0, {"requires_real": True})
    monkeypatch.setattr(assets, "acquire", lambda *a, **kw: None)
    monkeypatch.setattr(assets.OpenAI, "image", lambda *a: pytest.fail("Must not fabricate documentary image"))
    with pytest.raises(ConfigurationRequired, match="authentic"):
        assets.acquire_scene(scene, tmp_path / "a.png", 0, settings, first(), "v")


def test_ai_failure_has_no_placeholder_fallback(monkeypatch, tmp_path):
    from factory import assets
    from factory.providers import ConfigurationRequired
    settings = db.office(first())["settings"]
    scene = {"narration": "Space"} | assets.visual_plan("Space", "Space", settings, 0)
    def fail(*args):
        raise ConfigurationRequired("Image model access denied")
    monkeypatch.setattr(assets.OpenAI, "image", fail)
    monkeypatch.setattr(assets, "acquire", lambda *a, **kw: pytest.fail("No fallback"))
    with pytest.raises(ConfigurationRequired):
        assets.acquire_scene(scene, tmp_path / "a.png", 0, settings, first(), "v")


def test_stop_cancels_running_and_employee_reports():
    id = engine.enqueue(first(), True, ai_visuals=True)
    assert db.rows("reports", first(), id)[0]["data"]["role"] == "EDITOR"
    with db.connection() as c:
        c.execute("UPDATE jobs SET status='RUNNING' WHERE id=?", (id,))
        c.execute("UPDATE employee_states SET status='WORKING' WHERE office_id=?", (first(),))
    engine.set_mode(first(), "STOPPED")
    assert job(id)["status"] == "CANCELLED"
    with db.connection() as c:
        assert not c.execute("SELECT 1 FROM employee_states WHERE status='WORKING'").fetchone()


def test_visual_settings_survive_migration():
    with db.connection() as c:
        settings = db.office(first())["settings"] | {"visual_source_mode": "Real First"}
        c.execute("UPDATE office_settings SET payload=? WHERE office_id=?", (json.dumps(settings), first()))
    db.migrate()
    assert db.office(first())["settings"]["visual_source_mode"] == "Real First"


def test_single_scene_regeneration_preserves_other_images(monkeypatch, tmp_path):
    from factory import editing
    monkeypatch.setattr(editing, "MEDIA", tmp_path)
    id = engine.enqueue(first(), True, ai_visuals=True)
    directory = tmp_path / first() / id
    directory.mkdir(parents=True)
    for name in ("image-0.png", "image-1.png", "voice-0.wav", "final.mp4"):
        (directory / name).write_bytes(b"fixture")
    db.put("videos", first(), {"id": id, "test_mode": True, "scenes": [{"asset_source": "ai"}, {"asset_source": "ai"}], "file": "old", "qc": {}}, id, id)
    editing.restart(first(), id, 4, scene=1)
    assert (directory / "image-0.png").read_bytes() == b"fixture"
    assert not (directory / "image-1.png").exists()
    assert (directory / "voice-0.wav").exists()
    assert not (directory / "final.mp4").exists()
    assert job(id)["stage"] == 4 and job(id)["status"] == "QUEUED"


def test_pause_between_images_keeps_checkpoint(monkeypatch, tmp_path):
    from factory import assets
    monkeypatch.setattr(engine, "MEDIA", tmp_path)
    id = engine.enqueue(first(), True, ai_visuals=True)
    for _ in range(4):
        engine.tick()
    calls = []
    def image(scene, path, index, *args, **kwargs):
        calls.append(index)
        path.write_bytes(b"fixture")
        engine.set_mode(first(), "PAUSED")
        return {"asset_source": "ai", "provider": "mock", "rights": "CLEARED"}
    monkeypatch.setattr(assets, "acquire_scene", image)
    engine.tick()
    assert calls == [0]
    assert job(id)["status"] == "WAITING" and job(id)["stage"] == 4
    v = next(r["data"] for r in db.rows("videos", first()) if r["id"] == id)
    assert v["scenes"][0]["asset_source"] == "ai"


def oauth_fixture(monkeypatch, channel_status=200, channels=None, refresh=True):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-test")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-test")
    query = parse_qs(urlparse(youtube.oauth_start(first())).query)
    tokens = {"access_token": "access-test", "expires_in": 3600,
              "scope": youtube.SCOPES}
    if refresh:
        tokens["refresh_token"] = "refresh-test"
    monkeypatch.setattr(youtube.httpx, "post", lambda *a, **kw: httpx.Response(200, json=tokens))
    def get(url, **kw):
        assert kw["headers"]["Authorization"] == "Bearer access-test"
        if url.endswith("userinfo"):
            return httpx.Response(200, json={"email": "owner@example.com", "sub": "private-sub"})
        assert kw["params"] == {"part": "id,snippet,contentDetails", "mine": "true"}
        return httpx.Response(channel_status, json=channels if channels is not None else {
            "items": [{"id": "discovered-id", "snippet": {"title": "Discovered channel"}}]})
    monkeypatch.setattr(youtube.httpx, "get", get)
    return query


def test_oauth_actual_url_and_discovered_channel(monkeypatch, caplog):
    query = oauth_fixture(monkeypatch)
    assert set(youtube.REQUIRED_SCOPES.split()) <= set(query["scope"][0].split())
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        youtube.callback("code-test", query["state"][0])
    with db.connection() as c:
        row = c.execute("SELECT * FROM youtube_connections").fetchone()
        assert row["channel_id"] == "discovered-id"
        assert "refresh-test" in security.cipher().decrypt(row["encrypted"].encode()).decode()
    assert "items=1" in caplog.text and "owner@example.com" in caplog.text
    for value in ("access-test", "refresh-test", "secret-test", "code-test", "private-sub"):
        assert value not in caplog.text
    with pytest.raises(ValueError, match="state invalid"):
        youtube.callback("code-test", query["state"][0])


def test_oauth_google_error_not_hidden_and_redacted(monkeypatch, caplog):
    query = oauth_fixture(monkeypatch, 403, {"error": {"code": 403,
        "message": "insufficientPermissions access-test code-test secret-test",
        "errors": [{"reason": "insufficientPermissions"}]}})
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(ValueError, match="insufficientPermissions") as exc:
            youtube.callback("code-test", query["state"][0])
    assert "403" in str(exc.value)
    for value in ("access-test", "code-test", "secret-test"):
        assert value not in str(exc.value) + caplog.text


def test_oauth_empty_channel_distinct(monkeypatch):
    query = oauth_fixture(monkeypatch, channels={"items": []})
    with pytest.raises(ValueError, match="HTTP 200.*0 channels.*owner@example.com"):
        youtube.callback("code-test", query["state"][0])


def test_oauth_missing_refresh_not_saved(monkeypatch):
    query = oauth_fixture(monkeypatch, refresh=False)
    with pytest.raises(ValueError, match="no refresh token"):
        youtube.callback("code-test", query["state"][0])
    with db.connection() as c:
        assert c.execute("SELECT count(*) FROM youtube_connections").fetchone()[0] == 0


def test_oauth_access_log_hides_query():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/api/youtube/oauth/callback?code=code-test&state=state-test", "1.1", 400), None)
    youtube.OAuthAccessFilter().filter(record)
    assert "code-test" not in record.getMessage()
    assert "state-test" not in record.getMessage()


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
