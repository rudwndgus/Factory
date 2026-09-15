import json
import time
import base64
import io
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


def test_cloudflare_quota_error_is_clear_and_sanitized(monkeypatch, tmp_path):
    from factory import providers

    monkeypatch.setattr(providers, "secret", lambda name: "token-secret" if name.endswith("TOKEN") else "account-secret")
    monkeypatch.setattr(providers, "allowed_call", lambda *args: None)
    monkeypatch.setattr(providers, "reserve", lambda *args, **kwargs: None)
    monkeypatch.setattr(providers.httpx, "post", lambda *args, **kwargs: httpx.Response(
        429, json={"errors": [{"message": "quota exhausted token-secret"}]}
    ))
    with pytest.raises(providers.ImageProviderFailure, match="quota/free allocation") as error:
        providers.CloudflareWorkersAIImageProvider(first(), "video").image("space", tmp_path / "x.png")
    assert "token-secret" not in str(error.value)


def test_image_provider_fallback_is_explicit(monkeypatch, tmp_path):
    from factory import providers

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
