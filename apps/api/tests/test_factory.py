import json
import time
import base64
import io
import pytest
import httpx
import logging
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from factory import (
    analytics,
    database as db,
    engine,
    planning,
    reports,
    security,
    topics,
    verification,
    youtube,
)
from factory.providers import reserve, BudgetBlocked, similarity
from factory.main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from factory import main as main_module

    main_module.attempts.clear()
    monkeypatch.setattr(db, "DB", tmp_path / "test.sqlite3")
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OWNER_PASSWORD", "test-owner-password-123456")
    db.migrate()
    office = db.offices()[0]
    with db.connection() as connection:
        connection.execute(
            "UPDATE office_settings SET payload=? WHERE office_id=?",
            (json.dumps(office["settings"] | {"minimum_viral_score": 0}), office["id"]),
        )


def first():
    return db.offices()[0]["id"]


def test_visual_modes_and_provenance(monkeypatch, tmp_path):
    from factory import assets
    settings = db.office(first())["settings"]
    assert settings["visual_source_mode"] == "AI First"
    scene = {"narration": "Space is silent"} | assets.visual_plan("Space is silent", "Space", settings, 0, space_test=True)
    calls = []
    monkeypatch.setattr(assets, "generate_image", lambda *args: calls.append("ai") or {"model": "mock", "provider": "mock-ai"})
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
    monkeypatch.setattr(assets, "generate_image", lambda *a: pytest.fail("Must not fabricate documentary image"))
    with pytest.raises(ConfigurationRequired, match="authentic"):
        assets.acquire_scene(scene, tmp_path / "a.png", 0, settings, first(), "v")


def test_ai_failure_has_no_placeholder_fallback(monkeypatch, tmp_path):
    from factory import assets
    from factory.providers import ConfigurationRequired
    settings = db.office(first())["settings"]
    scene = {"narration": "Space"} | assets.visual_plan("Space", "Space", settings, 0)
    def fail(*args):
        raise ConfigurationRequired("Image model access denied")
    monkeypatch.setattr(assets, "generate_image", fail)
    monkeypatch.setattr(assets, "acquire", lambda *a, **kw: pytest.fail("No fallback"))
    with pytest.raises(ConfigurationRequired):
        assets.acquire_scene(scene, tmp_path / "a.png", 0, settings, first(), "v")


def test_cloudflare_flux_provider_decodes_image_and_limits_steps(monkeypatch, tmp_path):
    from PIL import Image
    from factory import providers

    encoded_file = io.BytesIO()
    Image.new("RGB", (16, 24), "navy").save(encoded_file, format="JPEG")
    encoded = base64.b64encode(encoded_file.getvalue()).decode()
    requested = {}
    monkeypatch.setattr(providers, "secret", lambda name: {
        "CLOUDFLARE_ACCOUNT_ID": "account-test",
        "CLOUDFLARE_API_TOKEN": "token-test",
    }.get(name, ""))
    monkeypatch.setattr(providers, "allowed_call", lambda *args: None)
    monkeypatch.setattr(providers, "reserve", lambda *args, **kwargs: None)
    def post(url, **kwargs):
        requested.update(url=url, **kwargs)
        return httpx.Response(200, json={"success": True, "result": {"image": encoded}})
    monkeypatch.setattr(providers.httpx, "post", post)
    output = tmp_path / "image.png"
    result = providers.CloudflareWorkersAIImageProvider(
        first(), "video", {"cloudflare_image_steps": 4, "image_seed_mode": "fixed", "image_fixed_seed": 42}
    ).image("cinematic Saturn", output)
    assert output.is_file() and Image.open(output).format == "PNG"
    assert requested["json"] == {"prompt": "cinematic Saturn", "seed": 42, "steps": 4}
    assert requested["headers"]["Authorization"] == "Bearer token-test"
    assert result["model"] == "@cf/black-forest-labs/flux-1-schnell"


def test_cloudflare_retries_without_seed_for_rest_schema_compatibility(monkeypatch, tmp_path):
    from PIL import Image
    from factory import providers

    encoded_file = io.BytesIO()
    Image.new("RGB", (8, 8), "black").save(encoded_file, format="JPEG")
    encoded = base64.b64encode(encoded_file.getvalue()).decode()
    payloads = []
    monkeypatch.setattr(providers, "secret", lambda name: "token" if name.endswith("TOKEN") else "account")
    monkeypatch.setattr(providers, "allowed_call", lambda *args: None)
    monkeypatch.setattr(providers, "reserve", lambda *args, **kwargs: None)
    def post(*args, **kwargs):
        payloads.append(dict(kwargs["json"]))
        if len(payloads) == 1:
            return httpx.Response(400, json={"errors": [{"message": "Additional or unevaluated properties '/seed' at '/' not allowed"}]})
        return httpx.Response(200, json={"result": {"image": encoded}})
    monkeypatch.setattr(providers.httpx, "post", post)
    result = providers.CloudflareWorkersAIImageProvider(first(), "v").image(
        "space", tmp_path / "image.png"
    )
    assert "seed" in payloads[0] and "seed" not in payloads[1]
    assert result["seed_supported"] is False and result["seed"] is None


def test_cloudflare_quota_error_is_clear_and_sanitized(monkeypatch, tmp_path):
    from factory import providers

    monkeypatch.setattr(providers, "secret", lambda name: "token-secret" if name.endswith("TOKEN") else "account-secret")
    monkeypatch.setattr(providers, "allowed_call", lambda *args: None)
    monkeypatch.setattr(providers, "reserve", lambda *args, **kwargs: None)
    monkeypatch.setattr(providers.httpx, "post", lambda *args, **kwargs: httpx.Response(
        429, json={"errors": [{"message": "quota exhausted token-secret"}]}
    ))
    with pytest.raises(providers.FreeQuotaWait, match="WAITING_FOR_FREE_QUOTA") as error:
        providers.CloudflareWorkersAIImageProvider(first(), "video").image("space", tmp_path / "x.png")
    assert "token-secret" not in str(error.value)


def test_image_provider_fallback_is_explicit(monkeypatch, tmp_path):
    from factory import providers
    monkeypatch.setenv("ZERO_COST_MODE", "false")

    calls = []
    class FakeProvider:
        def __init__(self, name):
            self.name = name
        def image(self, prompt, output):
            calls.append(self.name)
            if self.name == "cloudflare":
                raise providers.ImageProviderFailure("quota exhausted")
            return {"provider": "OpenAI Images", "model": "mock"}
    monkeypatch.setattr(
        providers,
        "image_provider",
        lambda name, *args: FakeProvider(name),
    )
    settings = {"image_provider": "cloudflare", "image_fallback_provider": "openai"}
    result = providers.generate_image("space", tmp_path / "x.png", first(), "v", settings)
    assert calls == ["cloudflare", "openai"] and result["fallback_from"] == "cloudflare"
    calls.clear()
    with pytest.raises(providers.ImageProviderFailure):
        providers.generate_image(
            "space", tmp_path / "x.png", first(), "v", settings, allow_fallback=False
        )
    assert calls == ["cloudflare"]


def test_storyboard_groups_long_narration_to_five_visuals():
    sentences = [f"sentence {index}" for index in range(8)]
    groups = engine.visual_scene_groups(sentences, 5)
    assert len(groups) == 5
    assert " ".join(group[2] for group in groups) == " ".join(sentences)


def test_curiosity_ai_run_records_five_cloudflare_assets(monkeypatch, tmp_path):
    from factory import assets

    monkeypatch.setattr(engine, "MEDIA", tmp_path)
    calls = []
    def generate(prompt, output, office_id, video_id, settings, allow_fallback=True):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mock-flux-image")
        calls.append(prompt)
        return {
            "provider": "Cloudflare Workers AI",
            "provider_id": "cloudflare",
            "model": "@cf/black-forest-labs/flux-1-schnell",
            "steps": 4,
            "seed": len(calls),
        }
    monkeypatch.setattr(assets, "generate_image", generate)
    engine.set_mode(first(), "RUNNING")
    video_id = engine.enqueue(first(), True, ai_visuals=True)
    for _ in range(5):
        engine.tick()
    video = next(row["data"] for row in db.rows("videos", first()) if row["id"] == video_id)
    assets_rows = db.rows("video_assets", first(), video_id)
    assert len(video["scenes"]) == 5 and len(calls) == 5 and len(assets_rows) == 5
    assert all(row["data"]["provider"] == "Cloudflare Workers AI" for row in assets_rows)


def test_stop_cancels_running_and_employee_reports():
    engine.set_mode(first(), "RUNNING")
    id = engine.enqueue(first(), True, ai_visuals=True)
    report = db.rows("reports", first(), id)[0]["data"]
    assert len(db.rows("reports", first(), id)) == 1
    assert len(report["departments"]) == 10
    assert next(s for s in report["departments"] if s["role"] == "EDITOR")["activities"]
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
    migrated = db.office(first())["settings"]
    assert migrated["visual_source_mode"] == "Real First"
    assert migrated["image_provider"] == "cloudflare"
    assert migrated["cloudflare_image_steps"] == 4
    assert migrated["max_images_per_short"] == 5


def test_single_scene_regeneration_preserves_other_images(monkeypatch, tmp_path):
    from factory import editing
    monkeypatch.setattr(editing, "MEDIA", tmp_path)
    engine.set_mode(first(), "RUNNING")
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
    engine.set_mode(first(), "RUNNING")
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


def test_budget_hard_stop(monkeypatch):
    monkeypatch.setenv("ZERO_COST_MODE", "false")
    monkeypatch.setenv("GLOBAL_DAILY_BUDGET", "100")
    monkeypatch.setenv("GLOBAL_MONTHLY_BUDGET", "100")
    id = first()
    with db.connection() as connection:
        settings = json.loads(connection.execute("SELECT payload FROM office_settings WHERE office_id=?", (id,)).fetchone()[0])
        settings.update(daily_budget=3, monthly_budget=20)
        connection.execute("UPDATE office_settings SET payload=? WHERE office_id=?", (json.dumps(settings), id))
    reserve(id, "v", "script", 2.9)
    with pytest.raises(BudgetBlocked):
        reserve(id, "v", "script", 0.2)
    assert len(db.rows("cost_events", id)) == 1


def test_global_budget(monkeypatch):
    monkeypatch.setenv("ZERO_COST_MODE", "false")
    monkeypatch.setenv("GLOBAL_DAILY_BUDGET", "0.10")
    monkeypatch.setenv("GLOBAL_MONTHLY_BUDGET", "100")
    office_id = first()
    with db.connection() as connection:
        settings = json.loads(connection.execute("SELECT payload FROM office_settings WHERE office_id=?", (office_id,)).fetchone()[0])
        settings.update(daily_budget=3, monthly_budget=20)
        connection.execute("UPDATE office_settings SET payload=? WHERE office_id=?", (json.dumps(settings), office_id))
    reserve(office_id, "v", "script", 0.08)
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


def test_one_report_per_video_and_archive():
    id = engine.enqueue(first(), True)
    reports.update_video_report(first(), id, "SCOUT", "소재 선정", "A topic", "done")
    reports.update_video_report(first(), id, "RESEARCHER", "출처 조사", "A topic", "checked", ["source"])
    rows = db.rows("reports", first(), id)
    assert len(rows) == 1
    r = rows[0]["data"]
    assert r["title"] == "업로드 대기 · A topic"
    assert len(r["departments"]) == 10
    assert sum(bool(s["activities"]) for s in r["departments"]) == 3
    reports.mark_uploaded(first(), id, 1_700_000_000)
    r = db.rows("reports", first(), id)[0]["data"]
    assert r["upload_date"] in r["title"] and "A topic" in r["title"]
    reports.archive(first(), rows[0]["id"])
    r = db.rows("reports", first(), id)[0]["data"]
    assert r["archived"] is True and r["confirmed_at"]


def test_report_exception_is_grouped_in_same_video_report():
    id = engine.enqueue(first(), True)
    reports.add_exception(first(), id, "ARTIST", "generation blocked")
    rows = db.rows("reports", first(), id)
    assert len(rows) == 1
    assert rows[0]["data"]["noteworthy"] == ["generation blocked"]


def client():
    c = TestClient(app)
    token = c.post(
        "/api/auth/login", json={"password": "test-owner-password-123456"}
    ).json()["token"]
    c.headers["Authorization"] = "Bearer " + token
    return c


def test_image_provider_endpoint_returns_protected_preview(monkeypatch, tmp_path):
    from factory import providers, main as main_module

    monkeypatch.setattr(main_module, "MEDIA", tmp_path)

    def generate(prompt, output, office_id, video_id, settings, allow_fallback=True):
        assert allow_fallback is False
        output.write_bytes(b"sample-image")
        return {"provider": "Cloudflare Workers AI", "model": "flux-test", "steps": 4, "seed": 7}

    monkeypatch.setattr(providers, "generate_image", generate)
    response = client().post(
        "/api/system/test-image-provider",
        json={"office_id": first(), "prompt": "cinematic space"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True and result["provider"] == "Cloudflare Workers AI"
    preview = client().get(result["preview_url"])
    assert preview.status_code == 200 and preview.content == b"sample-image"


def test_image_provider_endpoint_exposes_safe_configuration_error(monkeypatch):
    from factory import providers

    def fail(*args, **kwargs):
        raise providers.ImageProviderFailure("Configure Cloudflare credentials")

    monkeypatch.setattr(providers, "generate_image", fail)
    response = client().post(
        "/api/system/test-image-provider", json={"office_id": first()}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Configure Cloudflare credentials"}


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


def seed_topics(office_id, count=5):
    for index in range(count):
        id = f"topic-{index}"
        db.put(
            "topics",
            office_id,
            {
                "title": f"Surprising science subject number {index}",
                "summary": f"Authoritative source material {index}",
                "source_url": f"https://science.nasa.gov/example-{index}",
                "provider": "NASA",
                "status": "candidate",
                "category": "Science / Space",
                "source_authority": 0.98,
                "discovered_at": 1_800_000_000,
            },
            id=id,
        )


def seed_diverse_topics(office_id):
    categories = [
        ("Science / Space", "Why Saturn's rings are disappearing", "NASA"),
        ("Science / Space", "A black hole bends distant light", "NASA"),
        ("Mystery", "The mystery of stones that move alone", "USGS"),
        ("Mystery", "Why an ancient signal vanished", "NOAA"),
        ("Fresh", "A new robot learns to handle glass", "Ars Technica"),
        ("Fresh", "Tiny AI hardware changes field science", "Ars Technica"),
        ("Strange World", "The lake that suddenly turned pink", "NOAA"),
        ("Strange World", "An animal that survives frozen winters", "NOAA"),
        ("Evergreen", "Why metal feels colder than wood", "ScienceDaily"),
        ("Evergreen", "How shadows reveal the time", "ScienceDaily"),
        ("Experimental", "Could plants grow under red starlight", "ScienceDaily"),
        ("Experimental", "A laboratory tests silent levitation", "ScienceDaily"),
    ]
    for index, (category, title, provider) in enumerate(categories):
        db.put("topics", office_id, {
            "title": title, "summary": "Qualified public-source evidence",
            "source_url": f"https://science.nasa.gov/diverse-{index}",
            "provider": provider, "status": "candidate", "category": category,
            "source_authority": 0.95, "discovered_at": 1_800_000_000,
        }, id=f"diverse-{index}")


def test_daily_plan_is_persistent_unique_and_restart_safe():
    office_id = first()
    seed_topics(office_id)
    engine.set_mode(office_id, "RUNNING")
    plan = planning.ensure_plan(office_id, now=1_800_000_000)
    assert plan["target_video_count"] == 5
    assert len(plan["publish_slots"]) == 5
    assert len({slot["topic_id"] for slot in plan["publish_slots"]}) == 5
    assert len({slot["job_id"] for slot in plan["publish_slots"]}) == 5
    assert len({slot["scheduled_publish_at"] for slot in plan["publish_slots"]}) == 5
    with db.connection() as connection:
        original_jobs = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
    db.migrate()
    again = planning.ensure_plan(office_id, now=1_800_000_000)
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == original_jobs
    assert [slot["job_id"] for slot in again["publish_slots"]] == [
        slot["job_id"] for slot in plan["publish_slots"]
    ]


def test_add_to_queue_twice_creates_one_active_job():
    office_id = first()
    seed_topics(office_id, 1)
    first_job = engine.enqueue(office_id, topic_id="topic-0", manual_queue=True)
    with pytest.raises(engine.AlreadyQueued) as duplicate:
        engine.enqueue(office_id, topic_id="topic-0", manual_queue=True)
    assert duplicate.value.job_id == first_job
    assert len(engine.queue_items(office_id)) == 1


def test_concurrent_duplicate_enqueue_is_persistently_blocked():
    office_id = first()
    seed_topics(office_id, 1)

    def add():
        try:
            return ("queued", engine.enqueue(office_id, topic_id="topic-0", manual_queue=True))
        except engine.AlreadyQueued as exc:
            return ("already", exc.job_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: add(), range(2)))
    assert sorted(kind for kind, _ in results) == ["already", "queued"]
    assert len({job_id for _, job_id in results}) == 1


def test_pending_queue_item_can_be_removed_and_queued_again():
    office_id = first()
    seed_topics(office_id, 1)
    first_job = engine.enqueue(office_id, topic_id="topic-0", manual_queue=True)
    engine.remove_from_queue(office_id, first_job)
    assert engine.queue_items(office_id) == []
    second_job = engine.enqueue(office_id, topic_id="topic-0", manual_queue=True)
    assert second_job != first_job
    assert len(engine.queue_items(office_id)) == 1


def test_running_job_cannot_be_destructively_removed():
    office_id = first()
    seed_topics(office_id, 1)
    job_id = engine.enqueue(office_id, topic_id="topic-0", manual_queue=True)
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET status='RUNNING' WHERE id=?", (job_id,))
    with pytest.raises(ValueError, match="Cancel Production"):
        engine.remove_from_queue(office_id, job_id)


def test_daily_plan_uses_distinct_categories_and_semantic_topics():
    office_id = first()
    seed_diverse_topics(office_id)
    engine.set_mode(office_id, "RUNNING")
    plan = planning.ensure_plan(office_id, now=1_800_000_000)
    slots = plan["publish_slots"]
    assert len({slot["category_family"] for slot in slots}) == 5
    assert len({slot["scheduled_publish_at"] for slot in slots}) == 5
    for index, left in enumerate(slots):
        for right in slots[index + 1:]:
            assert topics.topic_similarity(left["topic_title"], right["topic_title"]) < 0.78
    assert all(slot.get("category_selection_reason") for slot in slots)
    assert all("score_breakdown" in slot for slot in slots)


def test_manual_queue_category_constrains_automatic_plan():
    office_id = first()
    seed_diverse_topics(office_id)
    manual_job = engine.enqueue(office_id, topic_id="diverse-0", manual_queue=True)
    engine.set_mode(office_id, "RUNNING")
    plan = planning.ensure_plan(office_id, now=1_800_000_000)
    assert plan["publish_slots"][0]["job_id"] == manual_job
    assert plan["publish_slots"][0]["category_family"] == "Science / Space"
    assert all(
        slot["category_family"] != "Science / Space"
        for slot in plan["publish_slots"][1:]
    )


def test_failed_daily_slot_gets_bounded_replacement():
    office_id = first()
    seed_topics(office_id, 6)
    engine.set_mode(office_id, "RUNNING")
    plan = planning.ensure_plan(office_id, now=1_800_000_000)
    slot = plan["publish_slots"][0]
    old_job = slot["job_id"]
    video = next(row["data"] for row in db.rows("videos", office_id) if row["id"] == old_job)
    video["status"] = "FAILED"
    db.put("videos", office_id, video, old_job, old_job)
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (old_job,))
    replaced = planning.reconcile(office_id, plan, now=1_800_000_000)
    replacement = replaced["publish_slots"][0]
    assert replacement["job_id"] != old_job
    assert replacement["replacement_count"] == 1
    assert replaced["replacement_jobs"] == 1


def test_content_media_block_gets_replacement_but_provider_block_does_not():
    office_id = first()
    seed_topics(office_id, 6)
    engine.set_mode(office_id, "RUNNING")
    plan = planning.ensure_plan(office_id, now=1_800_000_000)
    first_slot, second_slot = plan["publish_slots"][:2]
    with db.connection() as connection:
        connection.execute(
            "UPDATE jobs SET status='BLOCKED',error=? WHERE id=?",
            ("Scene requires authentic external imagery", first_slot["job_id"]),
        )
        connection.execute(
            "UPDATE jobs SET status='BLOCKED',error=? WHERE id=?",
            ("Configure CLOUDFLARE_API_TOKEN", second_slot["job_id"]),
        )
    first_old, second_old = first_slot["job_id"], second_slot["job_id"]
    result = planning.reconcile(office_id, plan, now=1_800_000_000)
    assert result["publish_slots"][0]["job_id"] != first_old
    assert result["publish_slots"][1]["job_id"] == second_old


def test_fact_verification_requires_real_source_evidence():
    claim = [{"claim": "Sound needs a medium", "source_url": "https://science.nasa.gov/a"}]
    verified = verification.verify_claims(
        claim,
        [{"title": "NASA", "source_url": "https://science.nasa.gov/a"}],
    )
    assert verified["facts_verified"] is True
    unsupported = verification.verify_claims([{"claim": "Unsupported"}], [])
    assert unsupported["verification_status"] == "UNSUPPORTED"
    assert unsupported["requires_review"] is True


def test_video_card_approval_makes_video_ready_for_automatic_upload():
    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    db.put(
        "videos",
        office_id,
        {
            "id": "review-video",
            "title": "Review me",
            "test_mode": False,
            "status": "REVIEW_REQUIRED",
            "qc": {"audio": True, "rights": True, "facts": False},
            "fact_check": {"requires_review": True},
        },
        "review-video",
        "review-video",
    )
    response = client().post(
        f"/api/offices/{office_id}/videos/review-video/review",
        json={"action": "approve"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "READY_FOR_APPROVAL" and body["facts_verified"] is True
    assert body["fact_check"]["manual_override"] is True


def test_approve_and_schedule_uses_slot_and_double_click_is_idempotent(monkeypatch, tmp_path):
    from factory import main as main_module

    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    scheduled = next(planning._future_configured_slots(office_id, time.time()))
    rendered = tmp_path / "ready.mp4"
    rendered.write_bytes(b"real-render-placeholder")
    monkeypatch.setattr(main_module, "MEDIA", tmp_path)
    db.put("videos", office_id, {
        "id": "approval-video", "title": "Approval video", "category": "Mystery",
        "actual_duration": 35.0, "test_mode": False, "status": "READY_FOR_APPROVAL",
        "facts_verified": True, "qc": {"audio": True, "rights": True, "facts": True},
        "file": "ready.mp4", "scheduled_publish_at": scheduled,
    }, "approval-video", "approval-video")
    calls = []
    monkeypatch.setattr(
        youtube.OfficialYouTube, "upload",
        lambda self, video, path, privacy, publish_at, owner_initiated=False: calls.append((privacy, publish_at))
        or {"youtube_id": "scheduled-youtube-id", "uploaded_at": time.time()},
    )
    first_response = client().post(
        f"/api/offices/{office_id}/videos/approval-video/approve-schedule",
        json={"confirmed": True},
    )
    assert first_response.status_code == 200
    assert first_response.json()["status"] == "SCHEDULED"
    assert first_response.json()["scheduled_publish_at"] == scheduled
    assert calls == [("private", scheduled)]
    second_response = client().post(
        f"/api/offices/{office_id}/videos/approval-video/approve-schedule",
        json={"confirmed": True},
    )
    assert second_response.status_code == 200
    assert second_response.json()["result"] == "ALREADY_SCHEDULED"
    assert calls == [("private", scheduled)]


def test_missed_publish_slot_moves_to_next_configured_slot(monkeypatch, tmp_path):
    from factory import main as main_module

    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    rendered = tmp_path / "missed.mp4"
    rendered.write_bytes(b"real-render-placeholder")
    monkeypatch.setattr(main_module, "MEDIA", tmp_path)
    past = "2020-01-01T09:00:00Z"
    db.put("videos", office_id, {
        "id": "missed-video", "title": "Missed slot", "category": "Fresh",
        "actual_duration": 35.0, "test_mode": False, "status": "READY_FOR_APPROVAL",
        "facts_verified": True, "qc": {"audio": True, "rights": True, "facts": True},
        "file": "missed.mp4", "scheduled_publish_at": past,
    }, "missed-video", "missed-video")
    monkeypatch.setattr(
        youtube.OfficialYouTube, "upload",
        lambda self, video, path, privacy, publish_at, owner_initiated=False: {
            "youtube_id": "rescheduled-id", "uploaded_at": time.time()
        },
    )
    response = client().post(
        f"/api/offices/{office_id}/videos/missed-video/approve-schedule",
        json={"confirmed": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["original_slot"] == past
    assert datetime.fromisoformat(body["final_publish_at"].replace("Z", "+00:00")).timestamp() > time.time()
    assert body["reschedule_reason"]


def test_concurrent_approve_clicks_create_one_youtube_upload(monkeypatch, tmp_path):
    from factory import main as main_module

    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    rendered = tmp_path / "concurrent.mp4"
    rendered.write_bytes(b"real-render-placeholder")
    monkeypatch.setattr(main_module, "MEDIA", tmp_path)
    scheduled = next(planning._future_configured_slots(office_id, time.time()))
    db.put("videos", office_id, {
        "id": "concurrent-approval", "title": "Concurrent approval",
        "category": "Evergreen", "actual_duration": 35.0, "test_mode": False,
        "status": "READY_FOR_APPROVAL", "facts_verified": True,
        "qc": {"audio": True, "rights": True, "facts": True},
        "file": "concurrent.mp4", "scheduled_publish_at": scheduled,
    }, "concurrent-approval", "concurrent-approval")
    calls = []

    def upload(*args, **kwargs):
        calls.append(1)
        time.sleep(0.1)
        return {"youtube_id": "one-upload-only", "uploaded_at": time.time()}

    monkeypatch.setattr(youtube.OfficialYouTube, "upload", upload)

    def approve():
        try:
            return main_module.approve_schedule(
                office_id, "concurrent-approval",
                main_module.ApproveScheduleInput(confirmed=True),
            ).get("result")
        except ValueError:
            return "IN_PROGRESS"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(), range(2)))
    assert len(calls) == 1
    assert "SCHEDULED" in results
    uploads = db.rows("videos", office_id, "concurrent-approval")
    assert uploads[0]["data"]["youtube_video_id"] == "one-upload-only"


def test_ready_video_uses_persistent_publish_slot(monkeypatch):
    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    settings = db.office(office_id)["settings"] | {"privacy": "public", "auto_upload": True}
    with db.connection() as connection:
        connection.execute(
            "UPDATE office_settings SET payload=? WHERE office_id=?",
            (json.dumps(settings), office_id),
        )
    scheduled = "2030-01-01T15:00:00Z"
    db.put(
        "videos",
        office_id,
        {
            "id": "ready-video",
            "title": "Ready",
            "test_mode": False,
            "status": "READY",
            "facts_verified": True,
            "qc": {"audio": True, "rights": True, "facts": True},
            "file": "ready.mp4",
            "scheduled_publish_at": scheduled,
        },
        "ready-video",
        "ready-video",
    )
    calls = []
    monkeypatch.setattr(
        youtube.OfficialYouTube,
        "upload",
        lambda self, video, path, privacy, publish_at: calls.append((privacy, publish_at))
        or {"youtube_id": "youtube-real-id", "uploaded_at": time.time()},
    )
    engine.publish_approved()
    assert calls == [("public", scheduled)]
    result = next(row["data"] for row in db.rows("videos", office_id) if row["id"] == "ready-video")
    assert result["status"] == "SCHEDULED"


def test_owner_review_video_never_auto_uploads(monkeypatch):
    office_id = first()
    engine.set_mode(office_id, "RUNNING")
    db.put("videos", office_id, {
        "id": "owner-gated", "title": "Owner gated", "test_mode": False,
        "status": "READY_FOR_APPROVAL", "facts_verified": True,
        "qc": {"audio": True, "rights": True, "facts": True}, "file": "owner.mp4",
    }, "owner-gated", "owner-gated")
    calls = []
    monkeypatch.setattr(youtube.OfficialYouTube, "upload", lambda *args, **kwargs: calls.append(1))
    engine.publish_approved()
    assert calls == []
    saved = next(row["data"] for row in db.rows("videos", office_id) if row["id"] == "owner-gated")
    assert saved["status"] == "READY_FOR_APPROVAL"


def test_upload_claim_blocks_a_second_process():
    office_id = first()
    youtube.claim_upload(office_id, "claim-video")
    with pytest.raises(ValueError, match="already in progress or complete"):
        youtube.claim_upload(office_id, "claim-video")
    youtube.finish_upload_claim(office_id, "claim-video", "needs_reconciliation")
    youtube.claim_upload(office_id, "claim-video")


def test_overnight_production_window_uses_office_timezone():
    from zoneinfo import ZoneInfo

    settings = {
        "production_window_enabled": True,
        "production_window_start": "18:00",
        "production_window_end": "09:00",
        "timezone": "America/New_York",
    }
    evening = datetime(2030, 1, 2, 19, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    morning = datetime(2030, 1, 3, 8, 59, tzinfo=ZoneInfo("America/New_York")).timestamp()
    daytime = datetime(2030, 1, 3, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    assert engine.production_window_open(settings, evening)
    assert engine.production_window_open(settings, morning)
    assert not engine.production_window_open(settings, daytime)


def test_overnight_shift_creates_one_next_day_batch_across_midnight():
    from zoneinfo import ZoneInfo

    office_id = first()
    office = db.office(office_id)
    settings = office["settings"] | {
        "timezone": "America/New_York",
        "production_window_enabled": True,
        "production_window_start": "18:00",
        "production_window_end": "09:00",
    }
    with db.connection() as connection:
        connection.execute(
            "UPDATE office_settings SET payload=? WHERE office_id=?",
            (json.dumps(settings), office_id),
        )
    seed_topics(office_id, 10)
    engine.set_mode(office_id, "RUNNING")
    evening = datetime(2030, 1, 2, 19, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    after_midnight = datetime(2030, 1, 3, 1, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    first_plan = planning.ensure_plan(office_id, now=evening)
    second_plan = planning.ensure_plan(office_id, now=after_midnight)
    assert first_plan["date"] == "2030-01-03"
    assert second_plan["date"] == first_plan["date"]
    assert [slot["job_id"] for slot in second_plan["publish_slots"]] == [
        slot["job_id"] for slot in first_plan["publish_slots"]
    ]


def test_viral_scoring_prefers_mystery_over_administration_news():
    exciting = topics.viral_potential({
        "title": "The Unexplained Ancient City That Suddenly Vanished",
        "category": "Mystery",
        "trend_signal": 0.7,
    })
    boring = topics.viral_potential({
        "title": "Agency Names University to Host Annual Meeting",
        "category": "Fresh",
        "trend_signal": 0,
    })
    assert exciting >= 0.75
    assert boring < 0.35


def test_default_ambient_bgm_is_generated_locally(tmp_path):
    from factory import media

    path = media._procedural_ambient_track(tmp_path)
    assert path.is_file() and path.stat().st_size > 100_000
    assert 15.5 <= media.duration(path) <= 16.5


def test_video_library_archive_restore_and_local_delete():
    office_id = first()
    db.put("videos", office_id, {
        "id": "library-video", "title": "Manage me", "status": "FAILED",
        "test_mode": False,
    }, "library-video", "library-video")
    response = client().post(f"/api/offices/{office_id}/videos/library-video/library/archive")
    assert response.status_code == 200 and response.json()["archived"] is True
    response = client().post(f"/api/offices/{office_id}/videos/library-video/library/restore")
    assert response.status_code == 200 and response.json()["archived"] is False
    response = client().delete(f"/api/offices/{office_id}/videos/library-video/library")
    assert response.status_code == 200
    assert not db.rows("videos", office_id, "library-video")


def test_uploaded_video_can_archive_but_not_local_delete():
    office_id = first()
    db.put("videos", office_id, {
        "id": "uploaded-video", "title": "Already on YouTube", "status": "UPLOADED_PRIVATE",
        "youtube_video_id": "youtube-id", "test_mode": False,
    }, "uploaded-video", "uploaded-video")
    assert client().post(
        f"/api/offices/{office_id}/videos/uploaded-video/library/archive"
    ).status_code == 200
    response = client().delete(f"/api/offices/{office_id}/videos/uploaded-video/library")
    assert response.status_code == 400
    assert "YouTube Studio" in response.json()["detail"]


def test_writer_rejects_narration_far_below_target(monkeypatch):
    from factory import providers

    provider = providers.OllamaProvider(settings={"ollama_model": "qwen3:4b"})
    short = {
        "title": "Short",
        "description": "Short",
        "category": "Science / Space",
        "format": "Explanation",
        "hook_style": "Question",
        "sentences": ["Only a few words here."] * 6,
        "claims": [],
        "visuals": [
            {"prompt": "space", "summary_ko": "우주", "kind": "cinematic",
             "requires_real": False, "reason": "context"}
        ] * 4,
    }
    monkeypatch.setattr(provider, "structured", lambda *args, **kwargs: short)
    with pytest.raises(ValueError, match="outside the target range"):
        provider.script({"title": "Space", "evidence": []}, {"duration": 35})


def test_analytics_creates_feedback_and_learning_profile():
    office_id = first()
    db.put(
        "videos",
        office_id,
        {
            "id": "performance-video",
            "title": "Performance",
            "category": "Science / Space",
            "format": "Explanation",
            "hook_style": "Question",
            "actual_duration": 34,
            "test_mode": False,
        },
        "performance-video",
        "performance-video",
    )
    analyzed = analytics.process_snapshot(
        office_id,
        "performance-video",
        {
            "viewCount": 100,
            "likeCount": 12,
            "commentCount": 3,
            "shares": 2,
            "estimatedMinutesWatched": 40,
            "averageViewDuration": 24,
            "averageViewPercentage": 70,
            "subscribersGained": 2,
        },
        uploaded_at=time.time() - 7 * 3600,
    )
    assert analyzed["views_per_hour"] > 0
    assert 0 <= analyzed["performance_score"] <= 100
    report = db.rows("reports", office_id, "performance-video")[0]["data"]
    assert report["performance_sections"][0]["window"] == "6-hour"
    assert db.rows("performance_profiles", office_id)


def test_normal_job_never_uses_fixed_test_script():
    office_id = first()
    seed_topics(office_id, 1)
    engine.set_mode(office_id, "RUNNING")
    job_id = engine.enqueue(office_id, topic_id="topic-0")
    engine.tick()
    video = next(row["data"] for row in db.rows("videos", office_id) if row["id"] == job_id)
    assert video["title"] != engine.TEST_SCRIPT["title"]
    assert video["test_mode"] is False


def test_streaming_wav_unknown_size_uses_actual_payload(tmp_path):
    from factory import media

    path = tmp_path / "streaming.wav"
    samples = b"\x00\x00" * 24000
    header = (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )
    path.write_bytes(header + samples)
    assert media.duration(path) == pytest.approx(1.0, abs=0.01)


def test_zero_cost_blocks_openai_even_when_key_exists(monkeypatch, tmp_path):
    from factory import providers

    monkeypatch.setenv("ZERO_COST_MODE", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-be-used")
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: pytest.fail("paid network call reached"))
    with pytest.raises(providers.PaidProviderBlocked, match="ZERO_COST_MODE"):
        providers.OpenAIImageProvider(first(), "v").image("space", tmp_path / "x.png")
    with pytest.raises(providers.PaidProviderBlocked, match="ZERO_COST_MODE"):
        providers.OpenAI(first(), "v").script({}, db.office(first())["settings"])


def test_zero_cost_cloudflare_failure_never_falls_back(monkeypatch, tmp_path):
    from factory import providers

    calls = []
    class Fake:
        def __init__(self, name): self.name = name
        def image(self, *_):
            calls.append(self.name)
            raise providers.ImageProviderFailure("free provider unavailable")
    monkeypatch.setenv("ZERO_COST_MODE", "true")
    monkeypatch.setattr(providers, "image_provider", lambda name, *args: Fake(name))
    with pytest.raises(providers.ImageProviderFailure):
        providers.generate_image("space", tmp_path / "x.png", first(), "v",
                                 {"image_provider": "cloudflare", "image_fallback_provider": "openai"})
    assert calls == ["cloudflare"]


def test_zero_cost_monetary_reservation_is_impossible(monkeypatch):
    from factory import providers

    monkeypatch.setenv("ZERO_COST_MODE", "true")
    with pytest.raises(providers.PaidProviderBlocked):
        providers.reserve(first(), "v", "script", 0.01)
    assert db.rows("cost_events", first()) == []


def test_writer_uses_local_ollama_structured_output(monkeypatch):
    from factory import providers

    model = "qwen3:8b"
    monkeypatch.setenv("ZERO_COST_MODE", "true")
    monkeypatch.setenv("OLLAMA_MODEL", model)
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"models": [{"name": model}]}, request=httpx.Request("GET", "http://ollama/api/tags")
    ))
    payload = {
        "title": "Local title", "description": "Local description",
        "category": "Science / Space", "format": "Explanation", "hook_style": "Question",
        "sentences": [
            "Each carefully written sentence contains ten useful spoken words today."
        ] * 7,
        "claims": [{"claim": "A", "source_url": "https://nasa.gov/a", "confidence": .9, "type": "fact"}],
        "visuals": [{"prompt": f"scene {i}", "summary_ko": f"장면 {i}", "kind": "cinematic",
                     "requires_real": False, "reason": "illustration"} for i in range(4)],
    }
    posted = []
    def post(url, **kwargs):
        posted.append((url, kwargs["json"]))
        return httpx.Response(200, json={"response": json.dumps(payload)}, request=httpx.Request("POST", url))
    monkeypatch.setattr(providers.httpx, "post", post)
    result = providers.llm_provider(first(), "v", db.office(first())["settings"] | {"ollama_model": model}).script(
        {"title": "Topic", "sources": []}, db.office(first())["settings"]
    )
    assert posted[0][0].endswith("/api/generate")
    assert posted[0][1]["model"] == model and posted[0][1]["format"] == providers.SCRIPT_SCHEMA
    assert result["provider"] == "Ollama local"


def test_tts_uses_local_kokoro_and_writes_wav(monkeypatch, tmp_path):
    import numpy as np
    from factory import providers

    class Audio:
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return np.ones(2400, dtype=np.float32) * 0.1
    class Result:
        audio = Audio()
    class Pipeline:
        def __call__(self, *args, **kwargs): return iter([Result()])
    provider = providers.KokoroTTSProvider(first(), "v", {"kokoro_voice": "af_heart"})
    monkeypatch.setattr(provider, "_pipeline", lambda: Pipeline())
    output = tmp_path / "voice.wav"
    provider.speak("Local voice", output)
    assert output.read_bytes()[:4] == b"RIFF"
    assert db.rows("usage_events", first(), "v")[0]["data"]["provider"] == "kokoro"
