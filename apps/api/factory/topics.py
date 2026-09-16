"""Modular, legally safe topic discovery and ranking.

YouTube entries are signals only. The factory never downloads or reuses their
media; all produced scripts, narration, visuals and edits remain original.
"""

import hashlib
import math
import random
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from . import database as db
from .providers import similarity


class RSSSource:
    def __init__(self, name, url, category, authority):
        self.name, self.url, self.category, self.authority = name, url, category, authority

    def discover(self, _office_id, _settings):
        response = httpx.get(self.url, timeout=20, follow_redirects=True)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        result = []
        for item in nodes[:15]:
            title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
            link = item.findtext("link") or ""
            if not link:
                atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.get("href", "") if atom_link is not None else ""
            summary = (
                item.findtext("description")
                or item.findtext("{http://www.w3.org/2005/Atom}summary")
                or ""
            )
            summary = re.sub("<[^>]+>", " ", summary)
            if title.strip() and link.startswith("http"):
                result.append(
                    {
                        "title": title.strip(),
                        "source_url": link,
                        "summary": summary[:8000],
                        "provider": self.name,
                        "status": "candidate",
                        "category": self.category,
                        "source_authority": self.authority,
                        "trend_signal": 0.0,
                        "signal_only": False,
                        "discovered_at": time.time(),
                    }
                )
        return result


class YouTubeTrendSource:
    name = "YouTube Trends"

    def discover(self, office_id, settings):
        from .youtube import access

        response = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            headers={"Authorization": "Bearer " + access(office_id)},
            params={
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": settings.get("youtube_trend_region", "US"),
                "maxResults": 20,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"YouTube trend metadata returned HTTP {response.status_code}")
        result = []
        for item in response.json().get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            views = int(stats.get("viewCount", 0))
            result.append(
                {
                    "title": snippet.get("title", "")[:250],
                    "source_url": "https://www.youtube.com/watch?v=" + item["id"],
                    "summary": snippet.get("description", "")[:4000],
                    "provider": self.name,
                    "status": "candidate",
                    "category": "Fresh",
                    "source_authority": 0.25,
                    "trend_signal": min(1.0, math.log10(max(10, views)) / 8),
                    "signal_only": True,
                    "youtube_metadata": {
                        "video_id": item["id"],
                        "views": views,
                        "duration": item.get("contentDetails", {}).get("duration"),
                        "region": settings.get("youtube_trend_region", "US"),
                    },
                    "discovered_at": time.time(),
                }
            )
        return [item for item in result if item["title"]]


SOURCE_FACTORIES = {
    "NASA": lambda: RSSSource("NASA", "https://www.nasa.gov/feed/", "Science / Space", 0.98),
    "NOAA": lambda: RSSSource("NOAA", "https://www.noaa.gov/rss.xml", "Strange World", 0.98),
    "USGS": lambda: RSSSource("USGS", "https://www.usgs.gov/feeds/news.xml", "Strange World", 0.98),
    "ScienceDaily": lambda: RSSSource("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml", "Science / Space", 0.76),
    "Ars Technica": lambda: RSSSource("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "Fresh", 0.76),
    "YouTube Trends": YouTubeTrendSource,
}

RESEARCH_HOSTS = (
    "nasa.gov", "noaa.gov", "usgs.gov", "nih.gov", "science.org",
    "sciencedaily.com", "arstechnica.com", "wikipedia.org",
)
USER_AGENT = "PixelShortsFactory/1.0 research (owner-operated)"

CATEGORY_FAMILIES = {
    "Fresh": ("fresh", "technology", "innovation", "ai", "robot", "phone", "gadget", "hardware"),
    "Science / Space": ("science", "space", "nasa", "astronomy", "planet", "star", "galaxy", "black hole", "telescope"),
    "Mystery": ("mystery", "mysterious", "unexplained", "vanish", "secret"),
    "Strange World": ("strange", "world", "earth", "ocean", "weather", "animal", "geology", "usgs", "noaa"),
    "Evergreen": ("evergreen", "history", "how", "why", "everyday"),
    "Experimental": ("experimental", "experiment", "speculative", "hypothesis"),
}


def normalize_category(category, title=""):
    """Map cosmetic category labels to one broad viewer-facing family."""
    haystack = f"{category} {title}".lower()
    direct = {name.lower(): name for name in CATEGORY_FAMILIES}
    if category.strip().lower() in direct:
        return direct[category.strip().lower()]
    for family, markers in CATEGORY_FAMILIES.items():
        if any(marker in haystack for marker in markers):
            return family
    return "Evergreen"


def topic_similarity(left, right):
    """Free lexical similarity combining title order and keyword overlap."""
    sequence = similarity(left, right)
    words_left = set(re.findall(r"[a-z0-9]+", left.lower()))
    words_right = set(re.findall(r"[a-z0-9]+", right.lower()))
    stop = {"the", "a", "an", "of", "in", "on", "and", "to", "why", "how"}
    words_left -= stop
    words_right -= stop
    union = words_left | words_right
    overlap = len(words_left & words_right) / len(union) if union else 0.0
    return max(sequence, overlap)


def retrieve_evidence(source):
    """Retrieve bounded public source text; source pages are data, never instructions."""
    url = source.get("source_url", "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if source.get("signal_only") or not any(
        host == allowed or host.endswith("." + allowed) for allowed in RESEARCH_HOSTS
    ):
        return source
    try:
        robots_response = httpx.get(
            f"{parsed.scheme}://{parsed.netloc}/robots.txt",
            headers={"User-Agent": USER_AGENT}, timeout=5, follow_redirects=True,
        )
        from urllib.robotparser import RobotFileParser
        robots = RobotFileParser()
        robots.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        if robots_response.status_code == 200:
            robots.parse(robots_response.text.splitlines())
            if not robots.can_fetch(USER_AGENT, url):
                return source | {"research_warning": "robots.txt disallows retrieval"}
        with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT},
                          timeout=20, follow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml")):
                return source | {"research_warning": "unsupported content type"}
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    break
                chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", "replace")
        raw = re.sub(r"(?is)<(script|style|nav|footer).*?>.*?</\1>", " ", raw)
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        return source | {
            "source_title": source.get("title", ""),
            "supporting_excerpt": text[:12000],
            "retrieved_at": time.time(),
            "retrieval_method": "bounded public HTTP GET",
        }
    except Exception as exc:
        return source | {"research_warning": type(exc).__name__}


def discover(office_id):
    office = db.office(office_id)
    settings = office["settings"]
    enabled = settings.get("topic_sources") or list(SOURCE_FACTORIES)
    found, warnings = [], []
    for name in enabled:
        factory = SOURCE_FACTORIES.get(name)
        if not factory:
            continue
        try:
            found.extend(factory().discover(office_id, settings))
        except Exception as exc:
            warnings.append(f"{name}: {type(exc).__name__}")
    if not found:
        raise RuntimeError("No configured topic sources were reachable")
    existing = db.rows("topics", office_id)
    added = 0
    for topic in found:
        duplicate = next(
            (row for row in existing if similarity(topic["title"], row["data"].get("title", "")) > 0.9),
            None,
        )
        if duplicate:
            continue
        topic["id"] = db.uid()
        db.put("topics", office_id, topic, id=topic["id"])
        existing.append({"id": topic["id"], "data": topic})
        added += 1
    db.event(office_id, f"Scout collected {added} new topics from {len(enabled)} configured sources")
    if warnings:
        db.event(office_id, "Some topic sources were unavailable: " + ", ".join(warnings), "warning")
    return added


def _profile_index(office_id, field, value):
    for row in db.rows("performance_profiles", office_id):
        data = row["data"]
        if data.get("dimension") == field and data.get("value") == value:
            return float(data.get("performance_index", 1.0))
    return 1.0


def score_topic(office_id, topic, settings, now=None):
    now = now or time.time()
    age_hours = max(0, (now - float(topic.get("discovered_at", now))) / 3600)
    freshness = max(0.0, 1.0 - age_hours / (24 * 14))
    title = topic.get("title", "")
    curiosity = min(1.0, 0.45 + (0.2 if "?" in title else 0) + min(len(title), 100) / 500)
    authority = float(topic.get("source_authority", 0.55))
    category = normalize_category(topic.get("category", "Evergreen"), title)
    category_weight = float(settings.get("category_weights", {}).get(category, 0)) / 100
    trend = float(topic.get("trend_signal", 0))
    history = _profile_index(office_id, "category", category)
    exploration_rate = float(settings.get("exploration_percentage", 15)) / 100
    stable_random = int(hashlib.sha256((topic.get("id", "") + str(now)[:5]).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    exploration = exploration_rate * stable_random
    components = {
        "freshness": freshness * 24,
        "curiosity": curiosity * 22,
        "source_authority": authority * 18,
        "category_weight": category_weight * 14,
        "trend_signal": trend * 12,
        "historical_performance": max(0.5, min(1.8, history)) / 1.8 * 8,
        "exploration_bonus": exploration * 10,
        "production_feasibility": (0.0 if topic.get("signal_only") else 1.0) * 5,
        "rights_visual_feasibility": (1.0 if authority >= 0.75 else 0.6) * 5,
    }
    return round(sum(components.values()), 3), {k: round(v, 3) for k, v in components.items()}


def ranked_candidates(office_id, excluded_ids=None):
    excluded_ids = set(excluded_ids or [])
    office = db.office(office_id)
    prior_videos = [v["data"] for v in db.rows("videos", office_id) if not v["data"].get("test_mode")]
    candidates = []
    for row in db.rows("topics", office_id):
        topic = row["data"] | {"id": row["id"]}
        if row["id"] in excluded_ids or topic.get("status") in ("used", "rejected"):
            continue
        if topic.get("signal_only"):
            continue
        duplicate = max((similarity(topic["title"], v.get("title", "")) for v in prior_videos), default=0)
        if duplicate >= office["settings"]["duplicate_threshold"]:
            continue
        score, breakdown = score_topic(office_id, topic, office["settings"])
        topic["category_family"] = normalize_category(
            topic.get("category", "Evergreen"), topic.get("title", "")
        )
        topic["topic_score"] = score
        topic["score_breakdown"] = breakdown | {"duplicate_similarity": round(duplicate, 3)}
        candidates.append(topic)
    return sorted(candidates, key=lambda item: (item["topic_score"], item.get("pinned", False)), reverse=True)


def diverse_candidates(office_id, count, excluded_ids=None, existing=None, rng=None):
    """Choose strong topics with category sampling without replacement first."""
    rng = rng or random.SystemRandom()
    existing = list(existing or [])
    ranked = ranked_candidates(office_id, excluded_ids)
    selected = []
    used_families = {normalize_category(t.get("category", ""), t.get("title", "")) for t in existing}
    used_titles = [t.get("title", "") for t in existing]
    weights = db.office(office_id)["settings"].get("category_weights", {})
    threshold = min(0.78, float(db.office(office_id)["settings"].get("duplicate_threshold", 0.85)))

    def qualified(items):
        return [
            item for item in items
            if all(topic_similarity(item["title"], title) < threshold for title in used_titles)
        ]

    while len(selected) < count:
        remaining = [item for item in ranked if item["id"] not in {x["id"] for x in selected}]
        clean = qualified(remaining)
        if clean:
            remaining = clean
        if not remaining:
            break
        by_family = {}
        for item in remaining:
            by_family.setdefault(item["category_family"], []).append(item)
        unused = [family for family in by_family if family not in used_families]
        choices = unused or list(by_family)
        category_weights = [max(1.0, float(weights.get(family, 5))) for family in choices]
        family = rng.choices(choices, weights=category_weights, k=1)[0]
        finalists = by_family[family][:5]
        floor = min(item["topic_score"] for item in finalists)
        candidate = rng.choices(
            finalists,
            weights=[max(1.0, item["topic_score"] - floor + 1.0) for item in finalists],
            k=1,
        )[0]
        candidate = candidate | {
            "category_selection_reason": (
                "Weighted category sample without replacement"
                if family not in used_families
                else "Diversity fallback: no unused qualified category family remained"
            ),
            "topic_selection_reason": "Weighted random choice from the strongest qualified candidates in this category",
        }
        selected.append(candidate)
        used_families.add(family)
        used_titles.append(candidate["title"])
    return selected
