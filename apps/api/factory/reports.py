"""One readable, durable production report per video."""

import os
import importlib.util
import shutil
import time
import httpx
from . import database as db, media
from .security import secret
from .config import DATA, ROLES
from .providers import llm_provider, tts_provider, zero_cost_mode, ConfigurationRequired

ROLE_STAGE = ["SCOUT", "RESEARCHER", "WRITER", "DIRECTOR", "ARTIST", "VOICE", "VOICE", "CUTTER", "RIGHTS / QC"]


def _report_id(video_id):
    return f"production-{video_id}"


def _date(timestamp=None):
    return time.strftime("%Y-%m-%d", time.localtime(timestamp or time.time()))


def _current(office_id, video_id, topic="제목 선정 중"):
    found = next((r for r in db.rows("reports", office_id, video_id) if r["id"] == _report_id(video_id)), None)
    if found:
        return found["data"]
    return dict(kind="production", video_id=video_id, topic=topic, upload_date=None,
        title=f"업로드 대기 · {topic}", status="제작 접수", archived=False, confirmed_at=None,
        departments=[dict(role=role, status="대기", activities=[], noteworthy=[]) for role in ROLES],
        noteworthy=[], performance_sections=[], latest_performance=None)


def update_video_report(office_id, video_id, role, heading, topic, summary, details=None, noteworthy=None):
    report = _current(office_id, video_id, topic)
    report["topic"] = topic or report["topic"]
    report["status"] = heading
    report["title"] = f"{report.get('upload_date') or '업로드 대기'} · {report['topic']}"
    section = next(s for s in report["departments"] if s["role"] == role)
    section["status"] = "완료" if not noteworthy else "확인 필요"
    defaults = {
        "EDITOR": ("Persistent SQLite scheduler", "LOCAL"),
        "SCOUT": ("Public RSS + YouTube metadata", "FREE QUOTA"),
        "RESEARCHER": ("Public HTTP evidence + Ollama", "LOCAL + PUBLIC"),
        "WRITER": ("Ollama local", "LOCAL"),
        "DIRECTOR": ("Deterministic local director", "LOCAL"),
        "ARTIST": ("Cloudflare Workers AI / public-domain source", "FREE QUOTA"),
        "VOICE": ("Kokoro + local subtitle timing", "LOCAL"),
        "CUTTER": ("FFmpeg", "LOCAL"),
        "RIGHTS / QC": ("Local QC + source records", "LOCAL"),
        "ANALYST / UPLOADER": ("Python + Ollama + YouTube API", "LOCAL + FREE QUOTA"),
    }
    provider, resource_class = defaults.get(role, ("Local factory", "LOCAL"))
    activity = dict(stage=heading, summary=summary, details=details or [], completed_at=time.time(),
                    provider=provider, resource_class=resource_class, monetary_usd=0.0, warnings=[])
    section["activities"] = [a for a in section["activities"] if a["stage"] != heading] + [activity]
    if noteworthy and noteworthy not in section["noteworthy"]:
        section["noteworthy"].append(noteworthy)
    report["noteworthy"] = [note for s in report["departments"] for note in s["noteworthy"]]
    db.put("reports", office_id, report, video_id, _report_id(video_id))
    return _report_id(video_id)


def employee_report(office_id, video_id, role, heading, topic, summary, details=None):
    return update_video_report(office_id, video_id, role, heading, topic, summary, details)


def stage_report(office_id, video_id, stage, video):
    sources = [s.get("source_url", "") for s in video.get("sources", [video.get("topic", {})]) if s]
    scenes = video.get("scenes", [])
    summaries = [
        ("소재 선정", "이 주제를 쇼츠 소재로 선정했습니다. 출처 조사로 전달했습니다.", sources),
        ("출처 조사", video.get("research_note", "원본 출처를 기록했습니다."), sources),
        ("대본 작성·사실 검증", "대본을 작성하고 각 주장을 수집된 근거와 대조했습니다.",
         video.get("script", {}).get("sentences", []) + [
             f"{claim.get('classification')}: {claim.get('claim')} (confidence {claim.get('confidence')})"
             for claim in video.get("fact_check", {}).get("claims", [])
         ]),
        ("장면 계획", f"{len(scenes)}개 장면을 계획했습니다. 상세 프롬프트는 Review room에서 볼 수 있습니다.", [f"장면 {i+1}: {s.get('visual_summary', s.get('narration', ''))}" for i, s in enumerate(scenes)]),
        ("이미지 제작", "장면별 이미지를 준비하고 출처를 기록했습니다.", [f"장면 {i+1}: {s.get('asset_source', 'unknown')} / {s.get('asset_provider', '')}" for i, s in enumerate(scenes)]),
        ("음성 제작", f"내레이션 생성 완료. 실제 길이 {video.get('actual_duration', 0):.1f}초입니다.", []),
        ("자막 제작", "장면별 음성 길이에 맞춰 자막을 배치했습니다.", []),
        ("영상 편집", "1080×1920 MP4로 이미지·음성·자막을 합치고 검수로 전달했습니다.", []),
        ("품질 검수", "Review room에서 최종 영상을 확인해주세요.", [f"{k}: {'통과' if v else '차단'}" for k, v in video.get("qc", {}).items()]),
    ]
    heading, summary, details = summaries[stage]
    update_video_report(office_id, video_id, ROLE_STAGE[stage], heading, video["title"], summary, details)
    report = _current(office_id, video_id, video["title"])
    section = next(item for item in report["departments"] if item["role"] == ROLE_STAGE[stage])
    activity = next(item for item in section["activities"] if item["stage"] == heading)
    provider_by_stage = {
        0: "Public RSS + YouTube metadata", 1: "Public HTTP evidence + Ollama local",
        2: "Ollama local", 3: "Deterministic local director", 4: "Cloudflare free quota / rights-cleared public source",
        5: "Kokoro local", 6: "Local narration timing", 7: "FFmpeg local", 8: "Local QC + retrieved evidence",
    }
    activity.update(provider=provider_by_stage[stage], resource_class="TEST FIXTURE" if video.get("test_mode") else (
        "FREE QUOTA" if stage in (0, 4) else "LOCAL"
    ), monetary_usd=0.0, warnings=[])
    db.put("reports", office_id, report, video_id, _report_id(video_id))
    if stage == 8:
        report = _current(office_id, video_id, video["title"])
        report["production_summary"] = {
            "topic_selection_reason": video.get("topic_selection_reason"),
            "topic_score": video.get("topic_score"),
            "topic_score_breakdown": video.get("topic_score_breakdown", {}),
            "fact_check": video.get("fact_check", {}),
            "script": video.get("script", {}),
            "scenes": video.get("scenes", []),
            "qc": video.get("qc", {}),
            "cost": 0.0 if zero_cost_mode() else sum(
                float(row["data"].get("estimated_usd", 0))
                for row in db.rows("cost_events", office_id, video_id)
            ),
            "scheduled_publish_at": video.get("scheduled_publish_at"),
            "review_policy": video.get("review_policy"),
            "review_reasons": video.get("review_reasons", []),
            "monetary_spend": 0.0,
            "free_resource_usage": [row["data"] for row in db.rows("usage_events", office_id, video_id)],
        }
        db.put("reports", office_id, report, video_id, _report_id(video_id))
        update_video_report(office_id, video_id, "ANALYST / UPLOADER", "게시 인계", video["title"],
            "테스트 영상이므로 업로드하지 않았습니다." if video.get("test_mode") else
            ("검토 예외가 있어 대표 승인을 기다립니다." if video.get("status") == "REVIEW_REQUIRED"
             else f"검증·QC를 통과해 {video.get('scheduled_publish_at') or '즉시'} 업로드 대기 중입니다."))


def add_exception(office_id, video_id, role, message):
    video = next((v["data"] for v in db.rows("videos", office_id) if v["id"] == video_id), {})
    return update_video_report(office_id, video_id, role, "특이사항 발생", video.get("title", "제목 선정 중"),
        "작업이 정상 완료되지 않아 대표 확인이 필요합니다.", noteworthy=message)


def mark_uploaded(office_id, video_id, uploaded_at=None):
    report = _current(office_id, video_id)
    report["upload_date"] = _date(uploaded_at)
    report["status"] = "업로드 완료"
    report["title"] = f"{report['upload_date']} · {report['topic']}"
    report.setdefault("youtube", {})["youtube_upload_at"] = uploaded_at or time.time()
    video = next((row["data"] for row in db.rows("videos", office_id) if row["id"] == video_id), {})
    report["youtube"].update(
        youtube_video_id=video.get("youtube_video_id") or video.get("youtube_id"),
        scheduled_publish_at=video.get("scheduled_publish_at"),
        published_at=video.get("published_at"),
    )
    db.put("reports", office_id, report, video_id, _report_id(video_id))
    update_video_report(office_id, video_id, "ANALYST / UPLOADER", "YouTube 업로드", report["topic"],
                        "공식 YouTube Data API 쿼터로 업로드했습니다.", ["provider: YouTube Data API", "resource: free quota", "monetary spend: $0.00"])


def append_performance(office_id, video_id, metrics):
    report = _current(office_id, video_id)
    age = float(metrics.get("age_hours", 0))
    milestones = [(168, "7-day"), (72, "72-hour"), (24, "24-hour"), (6, "6-hour")]
    label = next((name for hours, name in milestones if age >= hours), "early")
    section = {
        "window": label,
        "captured_at": metrics.get("captured_at", time.time()),
        "views": metrics.get("views", 0),
        "views_per_hour": metrics.get("views_per_hour", 0),
        "likes": metrics.get("likes", 0),
        "like_rate": metrics.get("like_rate", 0),
        "comments": metrics.get("comments", 0),
        "comment_rate": metrics.get("comment_rate", 0),
        "shares": metrics.get("shares", 0),
        "watch_time_minutes": metrics.get("watch_time_minutes", 0),
        "average_view_duration": metrics.get("average_view_duration", 0),
        "average_percentage_viewed": metrics.get("average_percentage_viewed", 0),
        "subscriber_gain": metrics.get("subscriber_gain", 0),
        "performance_score": metrics.get("performance_score"),
        "verdict": metrics.get("verdict"),
        "feedback": metrics.get("feedback", []),
    }
    sections = report.setdefault("performance_sections", [])
    report["performance_sections"] = [item for item in sections if item.get("window") != label] + [section]
    report["latest_performance"] = section
    report["status"] = f"사후관리 · {label} 분석 완료"
    db.put("reports", office_id, report, video_id, _report_id(video_id))
    update_video_report(
        office_id,
        video_id,
        "ANALYST / UPLOADER",
        f"사후관리 {label}",
        report["topic"],
        f"성과 점수 {metrics.get('performance_score')} · {metrics.get('verdict')}",
        metrics.get("feedback", []),
    )
    report = _current(office_id, video_id)
    section = next(item for item in report["departments"] if item["role"] == "ANALYST / UPLOADER")
    activity = next(item for item in section["activities"] if item["stage"] == f"사후관리 {label}")
    activity.update(provider="Python metrics + Ollama local", resource_class="LOCAL + YOUTUBE QUOTA",
                    monetary_usd=0.0, warnings=[])
    db.put("reports", office_id, report, video_id, _report_id(video_id))


def archive(office_id, report_id):
    found = next((r for r in db.rows("reports", office_id) if r["id"] == report_id), None)
    if not found:
        raise ValueError("Report not found")
    report = found["data"]
    report["archived"] = True
    report["confirmed_at"] = time.time()
    db.put("reports", office_id, report, found["video_id"], report_id)


def inspect(office_id):
    checks = {}
    try:
        with db.connection() as c:
            checks["database"] = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except Exception:
        checks["database"] = False
    checks["storage"] = os.access(DATA, os.W_OK)
    checks["disk_space"] = shutil.disk_usage(DATA).free > 1024**3
    try:
        media.run([media.ffmpeg(), "-version"])
        checks["ffmpeg"] = True
    except Exception:
        checks["ffmpeg"] = False
    checks["zero_cost_mode"] = zero_cost_mode()
    checks["paid_providers"] = "BLOCKED" if zero_cost_mode() else "OPTIONAL"
    try:
        checks["ollama"] = llm_provider(office_id, settings=db.office(office_id)["settings"]).health()["model"]
    except ConfigurationRequired as exc:
        checks["ollama"] = str(exc)
    try:
        checks["kokoro"] = tts_provider(office_id, settings=db.office(office_id)["settings"]).health()["voice"]
    except ConfigurationRequired as exc:
        checks["kokoro"] = str(exc)
    checks["espeak_ng"] = bool(
        shutil.which("espeak-ng") or shutil.which("espeak")
        or importlib.util.find_spec("espeakng_loader")
    )
    for name in ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                 "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PEXELS_API_KEY"]:
        try:
            checks[name] = "configured (not validated)" if secret(name) else "not configured"
        except Exception:
            checks[name] = "encryption configuration invalid"
    with db.connection() as c:
        checks["youtube_connected"] = bool(c.execute("SELECT 1 FROM youtube_connections WHERE office_id=?", (office_id,)).fetchone())
    try:
        checks["internet"] = httpx.get("https://www.nasa.gov", timeout=10).status_code < 500
    except httpx.HTTPError:
        checks["internet"] = False
    checks["scheduler"] = "active"
    checks["recent_errors"] = len(db.rows("errors", office_id)[:20])
    required_ready = (
        checks["database"] and checks["storage"] and checks["disk_space"]
        and checks["ffmpeg"] and checks["zero_cost_mode"]
        and checks["paid_providers"] == "BLOCKED"
        and not str(checks["ollama"]).startswith("LOCAL_")
        and not str(checks["kokoro"]).startswith("LOCAL_")
        and checks["espeak_ng"] and checks["youtube_connected"]
        and checks["internet"] and checks["scheduler"] == "active"
        and str(checks["CLOUDFLARE_ACCOUNT_ID"]).startswith("configured")
        and str(checks["CLOUDFLARE_API_TOKEN"]).startswith("configured")
        and str(checks["GOOGLE_CLIENT_ID"]).startswith("configured")
        and str(checks["GOOGLE_CLIENT_SECRET"]).startswith("configured")
    )
    result = {"kind": "inspection", "checks": checks, "monetary_spend": 0.0 if zero_cost_mode() else None,
        "overall": "Operational" if required_ready else "Configuration or inspection warnings"}
    id = db.put("system_inspections", office_id, result)
    db.event(office_id, "System inspection completed")
    return id


def scheduled_reports():
    for office in db.offices():
        plans = db.rows("daily_production_plans", office["id"])
        if plans:
            plan = plans[0]["data"]
            slots = plan.get("publish_slots", [])
            statuses = [slot.get("status") for slot in slots]
            videos = [
                row["data"]
                for row in db.rows("videos", office["id"])
                if row["id"] in {slot.get("video_id") for slot in slots}
            ]
            costs = sum(
                float(row["data"].get("estimated_usd", 0))
                for row in db.rows("cost_events", office["id"])
                if row.get("video_id") in {video.get("id") for video in videos}
            )
            daily = {
                "kind": "daily_summary",
                "title": f"{plan['date']} · 일일 생산 보고",
                "date": plan["date"],
                "archived": False,
                "target": plan["target_video_count"],
                "produced": sum(status in ("READY", "REVIEW_REQUIRED", "SCHEDULED", "PUBLISHED") for status in statuses),
                "scheduled": statuses.count("SCHEDULED"),
                "published": statuses.count("PUBLISHED"),
                "failed": sum(status in ("FAILED", "BLOCKED", "UPLOAD_BLOCKED") for status in statuses),
                "replacement_jobs": plan.get("replacement_jobs", 0),
                "total_api_cost": 0.0 if zero_cost_mode() else round(costs, 4),
                "free_resource_usage": [row["data"] for row in db.rows("usage_events", office["id"])],
                "warnings": plan.get("warnings", []),
                "videos_requiring_review": [v.get("title") for v in videos if v.get("status") == "REVIEW_REQUIRED"],
                "topics": [v.get("title") for v in videos],
            }
            db.put("reports", office["id"], daily, id=f"daily-report-{office['id']}-{plan['date']}")

        profiles = [row["data"] for row in db.rows("performance_profiles", office["id"])]
        if profiles:
            week = time.strftime("%Y-W%W", time.localtime())
            videos = [row["data"] for row in db.rows("videos", office["id"]) if not row["data"].get("test_mode")]
            scored = [video for video in videos if video.get("performance_score") is not None]
            weekly = {
                "kind": "weekly_summary",
                "title": f"{week} · 주간 성과 보고",
                "week": week,
                "archived": False,
                "published": sum(bool(video.get("youtube_video_id")) for video in videos),
                "average_performance_score": round(sum(v["performance_score"] for v in scored) / len(scored), 1) if scored else None,
                "performance_profiles": profiles,
                "recommended_experiments": [
                    "Retain the strongest category/hook as one factor",
                    "Keep the configured exploration percentage to avoid lock-in",
                    "Compare retention and response rates, not views alone",
                ],
            }
            db.put("reports", office["id"], weekly, id=f"weekly-report-{office['id']}-{week}")
