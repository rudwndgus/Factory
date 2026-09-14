"""Rights-aware still-image acquisition. Unknown licenses never enter auto production."""

import io
import json
import time
from pathlib import Path
import httpx
from PIL import Image
from .providers import Pexels
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


def acquire(query, path, index, test=False):
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
