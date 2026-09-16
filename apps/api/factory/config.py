import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
MEDIA = DATA / "media"
MEDIA.mkdir(exist_ok=True)
ROLES = [
    "EDITOR",
    "SCOUT",
    "RESEARCHER",
    "WRITER",
    "DIRECTOR",
    "ARTIST",
    "VOICE",
    "CUTTER",
    "RIGHTS / QC",
    "ANALYST / UPLOADER",
]
STAGES = [
    "DISCOVERING",
    "RESEARCHING",
    "WRITING",
    "STORYBOARDING",
    "ACQUIRING_MEDIA",
    "GENERATING_VOICE",
    "BUILDING_SUBTITLES",
    "RENDERING",
    "QUALITY_CHECK",
]
DEFAULTS = dict(
    zero_cost_mode=os.getenv("ZERO_COST_MODE", "true").lower() == "true",
    llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
    ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
    tts_provider=os.getenv("TTS_PROVIDER", "kokoro"),
    kokoro_voice=os.getenv("KOKORO_VOICE", "af_heart"),
    visual_source_mode="AI First",
    visual_style_preset="cinematic, mysterious, educational, high-contrast, clean, visually striking",
    image_provider=os.getenv("IMAGE_PROVIDER", "cloudflare"),
    image_fallback_provider=os.getenv("IMAGE_FALLBACK_PROVIDER", "none"),
    cloudflare_image_model=os.getenv(
        "CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell"
    ),
    cloudflare_image_steps=int(os.getenv("CLOUDFLARE_IMAGE_STEPS", "4")),
    image_seed_mode="random",
    image_fixed_seed=1,
    max_images_per_short=5,
    direction="Discover something surprising in under one minute.",
    language="English",
    audience="Global curious viewers",
    duration=35,
    videos_per_day=5,
    upload_times=["10:00", "12:30", "15:00", "17:30", "20:00"],
    timezone="UTC",
    production_window_enabled=False,
    production_window_start="18:00",
    production_window_end="09:00",
    background_music_enabled=True,
    background_music_volume=0.06,
    shorts_music_strategy="safe_ambient",
    content_profile="Viral Curiosity",
    minimum_viral_score=0.35,
    review_policy="Review Exceptions Only",
    # Kept for backwards-compatible settings payloads. New code uses review_policy.
    review_mode=True,
    auto_upload=True,
    production_lead_minutes=180,
    missed_slot_delay_minutes=30,
    replacement_cutoff_minutes=180,
    max_replacements_per_slot=2,
    exploration_percentage=20,
    youtube_trend_region="US",
    topic_sources=[
        "NASA", "NOAA", "USGS", "ScienceDaily", "Ars Technica",
        "Live Science", "Smithsonian Smart News", "Atlas Obscura",
        "Google News Curiosity", "YouTube Trends",
    ],
    daily_budget=0.0,
    monthly_budget=0.0,
    duplicate_threshold=0.85,
    freeze_weights=True,
    privacy="private",
    category_weights={
        "Fresh": 20,
        "Science / Space": 15,
        "Mystery": 30,
        "Strange World": 25,
        "Evergreen": 7,
        "Experimental": 3,
    },
)
