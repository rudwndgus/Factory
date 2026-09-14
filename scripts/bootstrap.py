"""Create local secrets once, without printing them or overwriting existing values."""

import secrets
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
env = ROOT / ".env"
if not env.exists():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    text = text.replace(
        "MASTER_KEY=\n", "MASTER_KEY=" + Fernet.generate_key().decode() + "\n"
    )
    text = text.replace(
        "OWNER_PASSWORD=\n", "OWNER_PASSWORD=" + secrets.token_urlsafe(24) + "\n"
    )
    env.write_text(text, encoding="utf-8")
    print(
        "Created .env with unique encryption key and owner password. Read OWNER_PASSWORD locally to sign in."
    )
else:
    print("Existing .env preserved.")
from PIL import Image, ImageDraw

for size in (192, 512):
    im = Image.new("RGB", (size, size), "#f0f4e9")
    d = ImageDraw.Draw(im)
    for x, y, col in [
        (100, 100, "#64835e"),
        (272, 100, "#93ab79"),
        (100, 272, "#bdceaa"),
        (272, 272, "#64835e"),
    ]:
        d.rectangle(
            tuple(int(v * size / 512) for v in (x, y, x + 139, y + 139)), fill=col
        )
    im.save(ROOT / f"apps/web/public/icon-{size}.png")
