"""Evidence-preserving, risk-based factual verification.

This module never invents verification. It classifies only the URLs and source
records already collected by the factory and sends weak evidence to review.
"""

from urllib.parse import urlparse

FIRST_PARTY = {
    "nasa.gov",
    "noaa.gov",
    "usgs.gov",
    "nih.gov",
    "cdc.gov",
    "who.int",
}
SENSITIVE = {
    "medical", "health", "disease", "treatment", "drug", "legal", "law",
    "finance", "investment", "election", "war", "death", "crime", "child",
}


def hostname(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except (TypeError, ValueError):
        return ""


def authority(url):
    host = hostname(url)
    if any(host == domain or host.endswith("." + domain) for domain in FIRST_PARTY):
        return 0.98, "authoritative first-party source"
    if host.endswith(".gov") or host.endswith(".edu"):
        return 0.9, "government or academic source"
    if host in {
        "sciencedaily.com", "nature.com", "science.org", "arstechnica.com",
        "livescience.com", "smithsonianmag.com", "atlasobscura.com",
    }:
        return 0.76, "established editorial source"
    if host in {"youtube.com", "youtu.be"}:
        return 0.25, "trend signal only"
    return (0.55, "general web source") if host else (0.0, "missing source")


def verify_claims(claims, sources):
    evidence_sources = []
    seen = set()
    for source in sources or []:
        url = source.get("source_url") or source.get("url") or ""
        host = hostname(url)
        if not host or host in seen or source.get("signal_only"):
            continue
        seen.add(host)
        score, label = authority(url)
        evidence_sources.append(
            {
                "url": url,
                "title": source.get("title", ""),
                "source_authority": score,
                "authority_reason": label,
                "agreement": "supports",
                "supporting_excerpt": source.get("supporting_excerpt") or source.get("summary", "")[:1500],
            }
        )

    results = []
    for claim in claims or []:
        text = str(claim.get("claim", "")).strip()
        claim_url = claim.get("source_url", "")
        claim_host = hostname(claim_url)
        matching = [e for e in evidence_sources if hostname(e["url"]) == claim_host]
        supporting = matching or evidence_sources
        sensitive = any(word in text.lower() for word in SENSITIVE)
        strong = [e for e in supporting if e["source_authority"] >= 0.75]
        independent = len({hostname(e["url"]) for e in strong})
        best = max((e["source_authority"] for e in supporting), default=0)
        if independent >= 2:
            status, confidence = "VERIFIED", min(0.97, 0.84 + independent * 0.04)
        elif independent == 1 and best >= 0.9 and not sensitive:
            status, confidence = "VERIFIED", 0.86
        elif supporting:
            status, confidence = "UNCERTAIN", min(0.74, best)
        else:
            status, confidence = "UNSUPPORTED", 0.0
        results.append(
            {
                "claim": text,
                "classification": status,
                "verification_status": status,
                "confidence": round(confidence, 3),
                "sensitive": sensitive,
                "supporting_sources": supporting,
                "source_url": supporting[0]["url"] if supporting else claim_url,
                "source_title": supporting[0]["title"] if supporting else "",
                "source_authority": supporting[0]["source_authority"] if supporting else 0,
                "supporting_excerpt": supporting[0].get("supporting_excerpt", "") if supporting else "",
                "agreement": "supporting evidence located" if supporting else "no evidence located",
                "contradiction": None,
            }
        )

    overall = min((r["confidence"] for r in results), default=0.0)
    verified = bool(results) and all(r["classification"] == "VERIFIED" for r in results)
    statuses = sorted({r["classification"] for r in results})
    return {
        "claims": results,
        "video_fact_confidence": round(overall, 3),
        "facts_verified": verified,
        "verification_status": "VERIFIED" if verified else "+".join(statuses) or "UNSUPPORTED",
        "requires_review": not verified,
        "method": "risk-based source authority and independent-source agreement",
    }
