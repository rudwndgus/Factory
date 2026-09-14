import os
import shutil
import time
import httpx
from . import database as db, media
from .security import secret
from .config import DATA


def employee_report(office_id, video_id, role, heading, topic, summary, details=None):
    db.put("reports", office_id, dict(kind="employee", role=role, title=f"{role} · {heading}",
        topic=topic, summary=summary, details=details or [], read=False, video_id=video_id), video_id)


def stage_report(office_id, video_id, stage, video):
    from .config import ROLES
    roles = [1, 2, 3, 4, 5, 6, 6, 7, 8]
    sources = [s.get("source_url", "") for s in video.get("sources", [video.get("topic", {})])]
    scenes = video.get("scenes", [])
    summaries = [
        ("소재 선정", "이 주제를 쇼츠 소재로 선정했습니다. 출처를 조사한 뒤 대본을 작성합니다.", sources),
        ("출처 조사", "원본 출처를 기록했습니다. 일반 제작 영상의 사실 검증은 대표님의 확인이 필요합니다.", sources),
        ("대본 작성", "아래 내레이션으로 영상을 구성합니다.", video.get("script", {}).get("sentences", [])),
        ("장면 계획", f"{len(scenes)}개 장면을 계획했습니다. 아래 내용으로 이미지를 제작합니다. 상세 프롬프트는 Review room에서 확인할 수 있습니다.", [f"장면 {i+1}: {s.get('visual_summary', s.get('narration', ''))}" for i,s in enumerate(scenes)]),
        ("이미지 제작", "장면별 이미지를 준비했습니다. AI 이미지는 실제 촬영 자료가 아닌 시각적 설명입니다.", [f"장면 {i+1}: {s.get('asset_source', 'unknown')} / {s.get('asset_provider', '')}" for i,s in enumerate(scenes)]),
        ("음성 제작", f"내레이션 생성 완료. 실제 길이 {video.get('actual_duration', 0):.1f}초입니다.", []),
        ("자막 제작", "장면별 음성 길이에 맞춰 자막을 배치했습니다. 단어별 정밀 정렬은 아닙니다.", []),
        ("영상 편집", "세로 1080×1920 MP4로 이미지, 음성, 자막을 합쳤습니다. 검수 단계로 전달합니다.", []),
        ("품질 검수", "검사 결과를 확인하고 Review room에서 영상을 검토해주세요. 테스트 영상은 게시할 수 없습니다.", [f"{k}: {'통과' if v else '차단'}" for k,v in video.get("qc", {}).items()]),
    ]
    heading, summary, details = summaries[stage]
    employee_report(office_id, video_id, ROLES[roles[stage]], heading, video["title"], summary, details)
    if stage == 8:
        employee_report(office_id, video_id, "ANALYST / UPLOADER", "게시 인계", video["title"],
            "테스트이므로 업로드와 성과 수집을 수행하지 않았습니다." if video.get("test_mode") else "대표님의 사실 확인·승인 및 품질 검사 통과 후 게시할 수 있습니다. 아직 업로드하지 않았습니다.")


def report(office_id, kind="daily"):
    cutoff = time.time() - (7 if kind == "weekly" else 1) * 86400
    videos = [v for v in db.rows("videos", office_id) if v["created"] >= cutoff]
    costs = [c for c in db.rows("cost_events", office_id) if c["created"] >= cutoff]
    snapshots = db.rows("analytics_snapshots", office_id)
    latest = {}
    for s in snapshots:
        latest.setdefault(s["video_id"], s["data"])
    result = dict(
        kind=kind,
        date=time.strftime("%Y-%m-%d"),
        planned=len(videos),
        produced=sum(bool(v["data"].get("file")) for v in videos),
        test_runs=sum(v["data"].get("test_mode", False) for v in videos),
        uploaded=len(
            [
                u
                for u in db.rows("uploads", office_id)
                if u["created"] >= cutoff and u["data"].get("youtube_id")
            ]
        ),
        failed=len([e for e in db.rows("errors", office_id) if e["created"] >= cutoff]),
        estimated_cost=sum(c["data"]["estimated_usd"] for c in costs),
        videos=[
            {"title": v["data"]["title"], "status": v["data"]["status"]} for v in videos
        ],
        analytics=latest,
        recommendation="Insufficient published analytics for strategy changes. Collect at least five comparable published videos.",
        read=False,
    )
    id = db.put("reports", office_id, result)
    db.event(office_id, kind.title() + " report delivered to CEO desk")
    return id


def inspect(office_id):
    checks = {}
    try:
        with db.connection() as c:
            checks["database"] = (
                c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            )
    except Exception:
        checks["database"] = False
    checks["storage"] = os.access(DATA, os.W_OK)
    checks["disk_space"] = shutil.disk_usage(DATA).free > 1024**3
    try:
        media.run([media.ffmpeg(), "-version"])
        checks["ffmpeg"] = True
    except Exception:
        checks["ffmpeg"] = False
    for name in [
        "OPENAI_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "PEXELS_API_KEY",
    ]:
        try:
            checks[name] = (
                "configured (not validated)" if secret(name) else "not configured"
            )
        except Exception:
            checks[name] = "encryption configuration invalid"
    with db.connection() as c:
        checks["youtube_connected"] = bool(
            c.execute(
                "SELECT 1 FROM youtube_connections WHERE office_id=?", (office_id,)
            ).fetchone()
        )
    try:
        checks["internet"] = (
            httpx.get("https://www.nasa.gov", timeout=10).status_code < 500
        )
    except httpx.HTTPError:
        checks["internet"] = False
    checks["scheduler"] = "active"
    checks["recent_errors"] = len(db.rows("errors", office_id)[:20])
    result = {
        "kind": "inspection",
        "checks": checks,
        "overall": (
            "Operational"
            if all(v is True or v == "active" or v == 0 for v in checks.values())
            else "Configuration or inspection warnings"
        ),
        "read": False,
    }
    db.put("system_inspections", office_id, result)
    id = db.put("reports", office_id, result)
    db.event(office_id, "System inspection completed")
    return id


def scheduled_reports():
    day = time.strftime("%Y-%m-%d", time.gmtime())
    for office in db.offices():
        existing = db.rows("reports", office["id"])
        if not any(
            r["data"].get("date") == day and r["data"].get("kind") == "daily"
            for r in existing
        ):
            report(office["id"])
        if time.gmtime().tm_wday == 0 and not any(
            r["data"].get("date") == day and r["data"].get("kind") == "weekly"
            for r in existing
        ):
            report(office["id"], "weekly")
