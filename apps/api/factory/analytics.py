"""Channel-relative performance analysis and learning profiles."""

import statistics
import time

from . import database as db


def _latest_by_video(office_id):
    latest = {}
    for row in db.rows("analytics_snapshots", office_id):
        latest.setdefault(row["video_id"], row["data"])
    return latest


def _median(values, fallback):
    values = [float(v) for v in values if v is not None]
    return statistics.median(values) if values else fallback


def analyze(office_id, video_id, metrics, uploaded_at=None, captured_at=None):
    captured_at = captured_at or time.time()
    uploaded_at = uploaded_at or captured_at
    age_hours = max((captured_at - uploaded_at) / 3600, 1 / 60)
    views = int(metrics.get("viewCount", metrics.get("views", 0)) or 0)
    likes = int(metrics.get("likeCount", 0) or 0)
    comments = int(metrics.get("commentCount", 0) or 0)
    shares = int(metrics.get("shares", 0) or 0)
    subscribers = int(metrics.get("subscribersGained", 0) or 0)
    watch_minutes = float(metrics.get("estimatedMinutesWatched", 0) or 0)
    average_duration = float(metrics.get("averageViewDuration", 0) or 0)
    average_percentage = float(metrics.get("averageViewPercentage", 0) or 0)
    derived = {
        "captured_at": captured_at,
        "age_hours": round(age_hours, 3),
        "views": views,
        "views_per_hour": round(views / age_hours, 3),
        "likes": likes,
        "like_rate": round(likes / views, 5) if views else 0,
        "comments": comments,
        "comment_rate": round(comments / views, 5) if views else 0,
        "shares": shares,
        "share_rate": round(shares / views, 5) if views else 0,
        "watch_time_minutes": watch_minutes,
        "average_view_duration": average_duration,
        "average_percentage_viewed": average_percentage,
        "subscriber_gain": subscribers,
    }
    peers = [value for key, value in _latest_by_video(office_id).items() if key != video_id]
    baselines = {
        "views_per_hour": _median([p.get("views_per_hour") for p in peers], derived["views_per_hour"] or 1),
        "like_rate": _median([p.get("like_rate") for p in peers], derived["like_rate"] or 0.01),
        "comment_rate": _median([p.get("comment_rate") for p in peers], derived["comment_rate"] or 0.001),
        "average_percentage_viewed": _median(
            [p.get("average_percentage_viewed") for p in peers],
            derived["average_percentage_viewed"] or 50,
        ),
        "subscriber_rate": _median(
            [p.get("subscriber_gain", 0) / max(p.get("views", 0), 1) for p in peers],
            subscribers / max(views, 1) or 0.001,
        ),
    }

    def ratio(value, baseline):
        return max(0.0, min(2.0, value / max(float(baseline), 1e-9)))

    components = {
        "velocity": ratio(derived["views_per_hour"], baselines["views_per_hour"]),
        "retention": ratio(average_percentage, baselines["average_percentage_viewed"]),
        "likes": ratio(derived["like_rate"], baselines["like_rate"]),
        "comments": ratio(derived["comment_rate"], baselines["comment_rate"]),
        "subscribers": ratio(subscribers / max(views, 1), baselines["subscriber_rate"]),
    }
    score = 50 * (
        components["velocity"] * 0.35
        + components["retention"] * 0.30
        + components["likes"] * 0.15
        + components["comments"] * 0.10
        + components["subscribers"] * 0.10
    )
    derived.update(
        channel_median={key: round(float(value), 5) for key, value in baselines.items()},
        score_components={key: round(value, 3) for key, value in components.items()},
        performance_score=round(max(0, min(100, score)), 1),
    )
    if derived["performance_score"] >= 65:
        verdict = "Strong relative performance"
    elif derived["performance_score"] < 40:
        verdict = "Weak relative performance"
    else:
        verdict = "Near recent channel baseline"
    derived["verdict"] = verdict
    derived["feedback"] = [
        f"View velocity is {components['velocity']:.2f}× the recent median.",
        f"Retention is {components['retention']:.2f}× the recent median.",
        f"Like response is {components['likes']:.2f}× the recent median.",
        "Use this result as one ranking factor; exploration remains enabled.",
    ]
    return metrics | derived


def update_learning_profiles(office_id):
    latest = _latest_by_video(office_id)
    videos = {
        row["id"]: row["data"]
        for row in db.rows("videos", office_id)
        if not row["data"].get("test_mode")
    }
    dimensions = {
        "category": lambda v: v.get("category", "Unknown"),
        "format": lambda v: v.get("format", "Unknown"),
        "hook_style": lambda v: v.get("hook_style", "Unknown"),
        "duration_range": lambda v: (
            "25-35 sec" if v.get("actual_duration", 0) <= 35 else "36-45 sec" if v.get("actual_duration", 0) <= 45 else "46-60 sec"
        ),
    }
    for dimension, getter in dimensions.items():
        groups = {}
        for video_id, metrics in latest.items():
            video = videos.get(video_id)
            if not video or metrics.get("performance_score") is None:
                continue
            groups.setdefault(getter(video), []).append(float(metrics["performance_score"]))
        for value, scores in groups.items():
            index = max(0.5, min(1.8, statistics.mean(scores) / 50))
            payload = {
                "dimension": dimension,
                "value": value,
                "sample_size": len(scores),
                "average_score": round(statistics.mean(scores), 2),
                "performance_index": round(index, 3),
                "updated_at": time.time(),
                "reason": "Relative channel performance; used as one topic ranking factor with exploration",
            }
            db.put(
                "performance_profiles",
                office_id,
                payload,
                id=f"profile-{dimension}-{value}"[:240],
            )


def process_snapshot(office_id, video_id, metrics, uploaded_at=None):
    analyzed = analyze(office_id, video_id, metrics, uploaded_at)
    try:
        from .providers import llm_provider
        analyzed["local_llm_analysis"] = llm_provider(
            office_id, video_id, db.office(office_id)["settings"]
        ).explain_performance(analyzed)
        analyzed["feedback"] = (
            analyzed["feedback"]
            + analyzed["local_llm_analysis"].get("lessons", [])
            + analyzed["local_llm_analysis"].get("experiments", [])
        )
    except Exception as exc:
        analyzed["local_llm_analysis"] = {"status": "deferred", "reason": type(exc).__name__}
    db.put("analytics_snapshots", office_id, analyzed, video_id)
    row = next((item for item in db.rows("videos", office_id) if item["id"] == video_id), None)
    if row:
        video = row["data"]
        video["analytics"] = analyzed
        video["performance_score"] = analyzed["performance_score"]
        if analyzed.get("youtube_status", {}).get("privacyStatus") == "public":
            video["status"] = "PUBLISHED"
            video["published_at"] = analyzed.get("youtube_published_at") or time.time()
        db.put("videos", office_id, video, video_id, video_id)
        from . import planning

        planning.update_video(office_id, video)
    update_learning_profiles(office_id)
    from . import reports

    reports.append_performance(office_id, video_id, analyzed)
    return analyzed
