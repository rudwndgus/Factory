"""Local readiness command used by scripts/setup-zero-cost.ps1."""

import json
import sys
from pathlib import Path

from .providers import OllamaProvider, KokoroTTSProvider


def main():
    output = Path(__file__).resolve().parents[3] / "data" / "provider-tests" / "kokoro-ready.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    ollama = OllamaProvider()
    print(json.dumps(ollama.health()))
    result = ollama.structured(
        'Return JSON with the single field "status" set to "ready".',
        {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    )
    if result.get("status") != "ready":
        raise RuntimeError("Ollama structured output check failed")
    kokoro = KokoroTTSProvider()
    print(json.dumps(kokoro.health()))
    kokoro.speak("Pixel Shorts Factory local voice is ready.", output)
    print(json.dumps({"ready": True, "voice_sample": str(output)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"READINESS_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
