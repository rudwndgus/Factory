"""Rights-aware still-image acquisition. Unknown licenses never enter auto production."""

import io
import json
import time
from pathlib import Path
import httpx
from PIL import Image
from .providers import Pexels, OpenAI, ConfigurationRequired
from . import media


def commons(query):
    response = httpx.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query + " filetype:bitmap",
            "gsrnamespace": 6,
            "gsrlimit": 5,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1080,
        },
        headers={
            "User-Agent": "PixelShortsFactory/1.0 (owner configured research tool)"
        },
        timeout=20,
    )
    if response.status_code != 200:
        return []
    result = []
    for page in response.json().get("query", {}).get("pages", {}).values():
        info = page.get("imageinfo", [{}])[0]
        meta = info.get("extmetadata", {})
        license = meta.get("LicenseShortName", {}).get("value", "")
        # Conservative V1 clearance: exact CC0 / public domain metadata; all other licenses need owner review.
        if license not in ("CC0", "Public domain"):
            continue
        result.append(
            dict(
                url=info.get("thumburl") or info.get("url"),
                source_url=info.get("descriptionurl"),
                author=meta.get("Artist", {}).get("value", ""),
                license=license,
                license_url=meta.get("LicenseUrl", {}).get("value", ""),
                rights="CLEARED",
                provider="Wikimedia Commons",
                rights_confidence=1,
                attribution=meta.get("Credit", {}).get("value", ""),
            )
        )
    return result


def acquire(query, path, index, test=False, allow_graphic=True):
    if not test:
        for provider in [commons, Pexels().search]:
            try:
                for item in provider(query):
                    url = item.get("url", "")
                    # Provider controlled image URLs only; no arbitrary owner URL fetching.
                    from urllib.parse import urlparse

                    if urlparse(url).hostname not in (
                        "upload.wikimedia.org",
                        "images.pexels.com",
                    ):
                        continue
                    with httpx.stream(
                        "GET",
                        url,
                        timeout=30,
                        headers={"User-Agent": "PixelShortsFactory/1.0"},
                    ) as response:
                        if response.status_code != 200:
                            continue
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 20 * 1024 * 1024:
                                raise ValueError("Image exceeds size limit")
                            chunks.append(chunk)
                    image = Image.open(io.BytesIO(b"".join(chunks))).convert("RGB")
                    image.thumbnail((2160, 3840))
                    image.save(path)
                    return item | {
                        "retrieved_at": time.time(),
                        "usage_status": "selected",
                    }
            except (httpx.HTTPError, ValueError, KeyError, OSError):
                continue
    if not allow_graphic:
        return None
    media.graphic(path, query, index)
    return dict(
        source_url=None,
        provider="local graphics",
        author="Pixel Shorts Factory",
        license="Original",
        rights="CLEARED",
        attribution_required=False,
        rights_confidence=1,
        retrieved_at=time.time(),
        usage_status="selected",
    )


SPACE_VISUALS = [
    "A lone astronaut outside a spacecraft above Earth's curved blue horizon, immense silent black vacuum, realistic suit fabric and visor reflections, strong sunlight and deep shadows. No explosion, no sound waves in vacuum.",
    "Inside a warm pressurized spacecraft cabin, an astronaut listening to another astronaut speaking, intimate cinematic close-up, realistic cabin equipment and human expression. Air is invisible, no floating molecule symbols.",
    "Extreme wide cinematic view of a tiny spacecraft isolated in the near vacuum between planets, distant sun rim-light on metallic hull, vast dark negative space, striking sense of silence and scale.",
    "An astronaut's helmet radio communicating with a distant spacecraft above Earth; restrained translucent blue electromagnetic signal arcs as an explicitly educational overlay, cinematic scientific illustration, not sound ripples or documentary evidence.",
    "Cinematic comparison diptych: warm illuminated pressurized spacecraft interior with astronauts talking on the left, silent exterior spacewalk with helmet radio on the right. Realistic textures, coherent lighting, no labels or text.",
]
SPACE_SUMMARIES = ["지구를 배경으로 우주선 밖에 있는 우주인: 우주의 고요함을 보여주는 도입",
    "공기가 있는 우주선 내부에서 대화하는 우주인: 소리가 전달되는 환경",
    "행성 사이에 홀로 떠 있는 작은 우주선: 진공과 압도적인 공간감",
    "우주인과 우주선 사이의 무선 통신: 전자기파를 푸른 교육용 효과로 시각화",
    "우주선 내부 대화와 외부 무선 통신을 나란히 비교하는 장면"]


def visual_plan(narration, title, settings, index, proposed=None, space_test=False):
    proposed = proposed if isinstance(proposed, dict) else {}
    kind = proposed.get("kind", "cinematic")
    if kind not in ("cinematic", "diagram", "documentary"):
        kind = "cinematic"
    subject = SPACE_VISUALS[index % len(SPACE_VISUALS)] if space_test else str(proposed.get("prompt") or narration)
    style = settings["visual_style_preset"]
    real = proposed.get("requires_real") is True or kind == "documentary"
    prompt = (f"Create a portrait still for an educational Short. Topic: {title}. Scene: {subject}. "
              f"Visual style: {style}. Strong central composition for 9:16 crop, detailed textures, cinematic lighting. "
              "No captions, typography, watermark, logo, decorative orbit rings or placeholder graphics. "
              "Do not pretend this illustration is authentic documentary evidence. "
              + ("A scientific explanatory visual is appropriate; keep the physics accurate." if kind == "diagram" or (space_test and index >= 3)
                 else "Use a compelling concrete scene, not a chart, diagram or abstract engineering schematic."))
    return dict(image_prompt=prompt, visual_style=style, visual_kind=kind,
                visual_summary=SPACE_SUMMARIES[index % len(SPACE_SUMMARIES)] if space_test else str(proposed.get("summary_ko") or narration),
                requires_real=real, visual_reason=str(proposed.get("reason", "Educational scene illustration")),
                visual_query=str(proposed.get("query") or title), asset_source=None)


def acquire_scene(scene, path, index, settings, office_id, video_id, offline=False):
    metadata = {k: scene[k] for k in ("image_prompt", "visual_style", "visual_kind", "requires_real", "visual_reason")}
    if offline:
        return acquire(scene["narration"], path, index, True) | metadata | {"asset_source": "offline_fixture"}
    real_required = scene.get("requires_real", False)
    mode = settings["visual_source_mode"]
    prefer_real = real_required or mode == "Real First" or (mode == "Mixed" and index % 2 == 1)
    if prefer_real:
        external = acquire(scene["visual_query"], path, index, allow_graphic=False)
        if external:
            return external | metadata | {"asset_source": "external", "review_note": "Verify source identity and visual relevance before publication."}
        if real_required:
            raise ConfigurationRequired("Scene requires authentic external imagery. No suitable source was found; revise the scene or provide a verified image. AI substitution blocked.")
    generated = OpenAI(office_id, video_id).image(scene["image_prompt"], path)
    return generated | metadata | dict(asset_source="ai", provider="OpenAI Images", source_url=None,
        license="AI-generated illustration (not documentary evidence)", rights="CLEARED", author="AI generated",
        retrieved_at=time.time(), usage_status="selected", review_note="Owner must review accuracy and third-party rights; AI origin does not guarantee clearance.")


def local_track(folder):
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in (".wav", ".mp3", ".ogg", ".m4a"):
            continue
        metadata = path.with_suffix(".json")
        if not metadata.exists():
            continue
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
            if record.get("rights") == "CLEARED" and record.get("license"):
                return path, record
        except (ValueError, OSError):
            continue
    return None
