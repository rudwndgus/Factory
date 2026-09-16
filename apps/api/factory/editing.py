"""Owner overrides invalidate dependent artifacts and resume at the correct stage."""

from . import database as db, engine, planning
from .config import MEDIA


def restart(office_id, video_id, stage, scene=None, sentences=None, replacement=None):
    if db.office(office_id)["mode"] in ("EMERGENCY_STOP", "MAINTENANCE"):
        raise ValueError("Factory mode blocks regeneration")
    if not engine.LOCK.acquire(blocking=False):
        raise ValueError(
            "A stage is running; pause and retry the edit after it completes"
        )
    try:
        entry = next(
            (r for r in db.rows("videos", office_id) if r["id"] == video_id), None
        )
        if not entry:
            raise ValueError("Video not found")
        v = entry["data"]
        if v.get("youtube_id") or db.rows("uploads", office_id, video_id):
            raise ValueError("Published or uploading videos cannot be regenerated")
        if stage not in (2, 3, 4, 5, 7):
            raise ValueError("Unsupported stage")
        if scene is not None and not 0 <= scene < len(v.get("scenes", [])):
            raise ValueError("Scene index invalid")
        directory = (MEDIA / office_id / video_id).resolve()
        if not directory.is_relative_to(MEDIA):
            raise ValueError("Invalid media directory")
        targets = ["final.mp4", "preview.jpg", "captions.ass", "concat.txt"]
        for i in range(len(v.get("scenes", []))):
            targets.append(f"scene-{i}.mp4")
            if stage <= 4 and (scene is None or i == scene):
                targets.append(f"image-{i}.png")
                for key in ("asset_source", "asset_provider", "image_path"):
                    v["scenes"][i].pop(key, None)
            if stage <= 5 and stage != 4 and (scene is None or i == scene):
                targets.append(f"voice-{i}.wav")
        for name in targets:
            (directory / name).unlink(missing_ok=True)
        if replacement is not None:
            destination = directory / f"image-{scene}.png"
            replacement.save(destination)
            record = {
                "provider": "owner",
                "source_url": None,
                "author": "owner",
                "license": "Unverified owner upload",
                "rights": "UNSAFE",
                "rights_confidence": 0,
                "path": str(destination.relative_to(MEDIA)),
                "usage_status": "selected",
            }
            db.put("rights_records", office_id, record, video_id)
            db.put("video_assets", office_id, record, video_id)
        if stage == 4:
            stage = 4  # Existing unaffected assets are reused by the pipeline.
        if sentences is not None:
            v["script"]["sentences"] = sentences
            v["facts_verified"] = False
            stage = 3
            db.put("video_scripts", office_id, v["script"], video_id)
        for key in ("file", "preview", "qc"):
            v.pop(key, None)
        v["status"] = "QUEUED"
        db.put("videos", office_id, v, video_id, video_id)
        planning.update_video(office_id, v)
        with db.connection() as c:
            c.execute(
                "UPDATE jobs SET stage=?,status='QUEUED',attempts=0,next_run=0,error=NULL WHERE id=? AND office_id=?",
                (stage, video_id, office_id),
            )
        db.event(office_id, "Owner restarted stage " + str(stage + 1), "info", video_id)
    finally:
        engine.LOCK.release()
