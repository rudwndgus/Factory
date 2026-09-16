import json
import math
import os
import shutil
import subprocess
import struct
import textwrap
import time
import wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg


def ffmpeg():
    return os.getenv("FFMPEG_PATH") or imageio_ffmpeg.get_ffmpeg_exe()


def run(args, cwd=None, cancel=lambda: False):
    with subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        # communicate with timeout drains pipes while preserving cooperative cancellation.
        while True:
            try:
                output = process.communicate(timeout=0.3)[0]
                break
            except subprocess.TimeoutExpired:
                if cancel():
                    process.kill()
                    process.communicate()
                    raise InterruptedError("Render cancelled by factory control")
        if process.returncode:
            raise RuntimeError(output.decode(errors="replace")[-1200:])
    return output


def font(size):
    for name in [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def graphic(path, title, index):
    image = Image.new("RGB", (1080, 1920), "#112534")
    d = ImageDraw.Draw(image)
    for x in range(0, 1080, 60):
        d.line((x, 0, x, 1920), fill="#193340", width=1)
    for y in range(0, 1920, 60):
        d.line((0, y, 1080, y), fill="#193340", width=1)
    d.text(
        (85, 120),
        "AMAZING THINGS / " + str(index + 1).zfill(2),
        font=font(28),
        fill="#b2e7ba",
    )
    cx, cy = 540, 800
    for r in [120, 240, 360]:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline="#669b97", width=5)
    for i in range(9):
        angle = i * math.tau / 9 + index
        x = cx + 300 * math.cos(angle)
        y = cy + 300 * math.sin(angle)
        d.ellipse((x - 20, y - 20, x + 20, y + 20), fill="#ead586")
    d.text(
        (100, 1150),
        "\n".join(textwrap.wrap(title, 26)[:5]),
        font=font(54),
        fill="white",
        spacing=16,
    )
    d.text((100, 1700), "ORIGINAL EXPLANATORY GRAPHIC", font=font(24), fill="#88aaa6")
    image.save(path)


def local_voice(text, path):
    """Actual offline speech, not a silent or sine-wave substitute."""
    if os.name == "nt":
        script = Path(path).with_suffix(".ps1")
        txt = Path(path).with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        script.write_text(
            "Add-Type -AssemblyName System.Speech\n$speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer\n$speaker.Rate=0\n$speaker.SetOutputToWaveFile($args[1])\n$speaker.Speak([IO.File]::ReadAllText($args[0]))\n$speaker.Dispose()",
            encoding="utf-8",
        )
        run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                str(txt),
                str(path),
            ]
        )
    elif shutil.which("espeak-ng"):
        run(["espeak-ng", "-s", "155", "-w", str(path), text])
    else:
        raise RuntimeError("Offline TEST RUN narration needs Windows SAPI or espeak-ng")


def duration(path):
    import wave

    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.getnframes()
    reported = frames / rate
    actual_audio_bytes = max(0, Path(path).stat().st_size - 44)
    reported_bytes = frames * channels * width
    # Streaming WAV responses can legally leave the RIFF/data size as 0xffffffff.
    # Python's wave module treats that sentinel as real frames; use the actual
    # payload size when the header is impossible for the file on disk.
    if reported > 600 or reported_bytes > actual_audio_bytes * 1.1:
        reported = actual_audio_bytes / max(1, rate * channels * width)
    if not 0 < reported <= 600:
        raise RuntimeError("Narration WAV has an invalid duration")
    return reported


def ass_time(value):
    total = int(value * 100)
    return f"{total//360000}:{total//6000%60:02}:{total//100%60:02}.{total%100:02}"


def subtitles(directory, scenes):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,58,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,90,90,250,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    cursor = 0
    lines = []
    for scene in scenes:
        words = (
            scene["narration"]
            .replace("{", "")
            .replace("}", "")
            .replace("\\", "")
            .split()
        )
        chunks = [words[i : i + 7] for i in range(0, len(words), 7)]
        for i, chunk in enumerate(chunks):
            start = cursor + scene["duration"] * i / len(chunks)
            end = cursor + scene["duration"] * (i + 1) / len(chunks)
            lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,"
                + " ".join(chunk)
            )
        cursor += scene["duration"]
    (directory / "captions.ass").write_text(header + "\n".join(lines), encoding="utf-8")


def _procedural_ambient_track(directory):
    """Create a quiet original ambient bed locally; no third-party music rights."""
    path = directory / "factory-ambient-bed.wav"
    if path.exists():
        return path
    rate, seconds = 24000, 16
    frequencies = (110.0, 164.81, 220.0)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        frames = bytearray()
        for index in range(rate * seconds):
            t = index / rate
            fade = min(1.0, t / 2.0, (seconds - t) / 2.0)
            pulse = 0.72 + 0.28 * math.sin(math.tau * t / 8.0)
            sample = sum(
                math.sin(math.tau * frequency * t + harmonic * 0.7)
                for harmonic, frequency in enumerate(frequencies)
            ) / len(frequencies)
            frames.extend(struct.pack("<h", int(32767 * 0.13 * fade * pulse * sample)))
        output.writeframes(frames)
    return path


def render(directory, scenes, cancel=lambda: False, settings=None):
    from .assets import local_track
    from .config import ROOT

    pieces = []
    for i, scene in enumerate(scenes):
        if cancel():
            raise InterruptedError("Rendering cancelled")
        output = f"scene-{i}.mp4"
        pieces.append(output)
        if (directory / output).exists():
            continue
        frames = math.ceil(scene["duration"] * 25)
        vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.00035,1.12)':d={frames}:s=1080x1920:fps=25,setsar=1,format=yuv420p"
        log = run(
            [
                ffmpeg(),
                "-y",
                "-loop",
                "1",
                "-i",
                f"image-{i}.png",
                "-i",
                f"voice-{i}.wav",
                "-t",
                str(scene["duration"]),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                output,
            ],
            directory,
            cancel,
        )
        (directory / f"render-{i}.log").write_bytes(log)
    (directory / "concat.txt").write_text(
        "\n".join(f"file '{p}'" for p in pieces), encoding="utf-8"
    )
    settings = settings or {}
    music = local_track(ROOT / "media_library/music")
    if (
        not music
        and settings.get("background_music_enabled", True)
        and settings.get("shorts_music_strategy", "safe_ambient") == "safe_ambient"
    ):
        music = (
            _procedural_ambient_track(directory),
            {
                "rights": "CLEARED",
                "license": "Original procedural audio generated locally by Pixel Shorts Factory",
                "source": "local",
            },
        )
    sfx = local_track(ROOT / "media_library/sfx")
    args = [ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt"]
    filters = []
    labels = ["[0:a]"]
    audio_index = 1
    music_volume = float(settings.get("background_music_volume", 0.06))
    for track, volume, loop in [(music, music_volume, True), (sfx, 0.12, False)]:
        if not track:
            continue
        if loop:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(track[0])]
        filters.append(f"[{audio_index}:a]volume={volume}[a{audio_index}]")
        labels.append(f"[a{audio_index}]")
        audio_index += 1
    if filters:
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[mix]"
        )
        args += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[mix]"]
    else:
        args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    args += [
        "-vf",
        "ass=captions.ass",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "final.mp4",
    ]
    log = run(args, directory, cancel)
    (directory / "render.log").write_bytes(log)
    run(
        [
            ffmpeg(),
            "-y",
            "-ss",
            "1",
            "-i",
            "final.mp4",
            "-frames:v",
            "1",
            "preview.jpg",
        ],
        directory,
        cancel,
    )
    return directory / "final.mp4"


def technical_qc(path):
    output = run(
        [
            ffmpeg(),
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=2:pix_th=0.10",
            "-af",
            "silencedetect=noise=-45dB:d=4",
            "-f",
            "null",
            "-",
        ]
    ).decode(errors="replace")
    return dict(
        resolution="1080x1920" in output,
        audio="Audio:" in output,
        nonempty=path.stat().st_size > 10000,
        no_black="black_start:" not in output,
        no_long_silence="silence_start:" not in output,
    )
