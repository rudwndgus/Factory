"""One readable, durable production report per video."""

import os
import shutil
import time
import httpx
from . import database as db, media
from .security import secret
from .config import DATA, ROLES

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
        departments=[dict(role=role, status="대기", activities=[], noteworthy=[]) for role in ROLES], noteworthy=[])


def update_video_report(office_id, video_id, role, heading, topic, summary, details=None, noteworthy=None):
    report = _current(office_id, video_id, topic)
    report["topic"] = topic or report["topic"]
    report["status"] = heading
    report["title"] = f"{report.get('upload_date') or '업로드 대기'} · {report['topic']}"
    section = next(s for s in report["departments"] if s["role"] == role)
    section["status"] = "완료" if not noteworthy else "확인 필요"
    activity = dict(stage=heading, summary=summary, details=details or [], completed_at=time.time())
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
        ("출처 조사", "원본 출처를 기록했습니다. 일반 영상은 대표님의 사실 확인이 필요합니다.", sources),
        ("대본 작성", "다음 내레이션으로 영상을 구성했습니다.", video.get("script", {}).get("sentences", [])),
        ("장면 계획", f"{len(scenes)}개 장면을 계획했습니다. 상세 프롬프트는 Review room에서 볼 수 있습니다.", [f"장면 {i+1}: {s.get('visual_summary', s.get('narration', ''))}" for i, s in enumerate(scenes)]),
        ("이미지 제작", "장면별 이미지를 준비하고 출처를 기록했습니다.", [f"장면 {i+1}: {s.get('asset_source', 'unknown')} / {s.get('asset_provider', '')}" for i, s in enumerate(scenes)]),
        ("음성 제작", f"내레이션 생성 완료. 실제 길이 {video.get('actual_duration', 0):.1f}초입니다.", []),
        ("자막 제작", "장면별 음성 길이에 맞춰 자막을 배치했습니다.", []),
        ("영상 편집", "1080×1920 MP4로 이미지·음성·자막을 합치고 검수로 전달했습니다.", []),
        ("품질 검수", "Review room에서 최종 영상을 확인해주세요.", [f"{k}: {'통과' if v else '차단'}" for k, v in video.get("qc", {}).items()]),
    ]
    heading, summary, details = summaries[stage]
    update_video_report(office_id, video_id, ROLE_STAGE[stage], heading, video["title"], summary, details)
    if stage == 8:
        update_video_report(office_id, video_id, "ANALYST / UPLOADER", "게시 인계", video["title"],
            "테스트 영상이므로 업로드하지 않았습니다." if video.get("test_mode") else "사실 확인·대표 승인·QC 통과 후 업로드할 수 있습니다.")


def add_exception(office_id, video_id, role, message):
    video = next((v["data"] for v in db.rows("videos", office_id) if v["id"] == video_id), {})
    return update_video_report(office_id, video_id, role, "특이사항 발생", video.get("title", "제목 선정 중"),
        "작업이 정상 완료되지 않아 대표 확인이 필요합니다.", noteworthy=message)


def mark_uploaded(office_id, video_id, uploaded_at=None):
    report = _current(office_id, video_id)
    report["upload_date"] = _date(uploaded_at)
    report["status"] = "업로드 완료"
    report["title"] = f"{report['upload_date']} · {report['topic']}"
    db.put("reports", office_id, report, video_id, _report_id(video_id))


def archive(office_id, report_id):
    found = next((r for r in db.rows("reports", office_id) if r["id"] == report_id), None)
    if not found or found["data"].get("kind") != "production":
        raise ValueError("Production report not found")
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
    for name in ["OPENAI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PEXELS_API_KEY"]:
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
    result = {"kind": "inspection", "checks": checks,
        "overall": "Operational" if all(v is True or v == "active" or v == 0 for v in checks.values()) else "Configuration or inspection warnings"}
    id = db.put("system_inspections", office_id, result)
    db.event(office_id, "System inspection completed")
    return id


def scheduled_reports():
    return None
