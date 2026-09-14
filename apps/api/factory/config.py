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
    visual_source_mode="AI First",
    visual_style_preset="cinematic, mysterious, educational, high-contrast, clean, visually striking",
    direction="Discover something surprising in under one minute.",
    language="English",
    audience="Global curious viewers",
    duration=35,
    videos_per_day=3,
    upload_times=["09:00", "15:00", "21:00"],
    timezone="UTC",
    review_mode=True,
    auto_upload=False,
    daily_budget=3.0,
    monthly_budget=20.0,
    duplicate_threshold=0.85,
    freeze_weights=True,
    privacy="private",
    category_weights={
        "Fresh": 35,
        "Science / Space": 20,
        "Mystery": 15,
        "Strange World": 15,
        "Evergreen": 10,
        "Experimental": 5,
    },
)
