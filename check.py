#!/usr/bin/env python3
"""Small real-render smoke check for the FFglitch pipeline."""
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parent
(ROOT / "cache").mkdir(exist_ok=True)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)


def difference(a, b):
    with Image.open(a) as left, Image.open(b) as right:
        delta = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        return sum(ImageStat.Stat(delta).mean) / 3


with tempfile.TemporaryDirectory(dir=ROOT / "cache") as temporary:
    os.environ["MOSH_CLIPS"] = temporary
    import server

    missing = server.missing_tool_message()
    if missing:
        raise SystemExit(missing)

    server.CACHE = temporary
    server.SEGCACHE = str(Path(temporary) / "segments")
    Path(server.SEGCACHE).mkdir()
    a, b = Path(temporary) / "a.mp4", Path(temporary) / "b.mp4"
    run(server.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v",
        "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(a))
    run(server.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=30", "-vf", "hue=h=180", "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y", str(b))

    first = {"clip": "a.mp4", "start": 0, "dur": 3,
             "effect": "bloom", "params": {"strength": 40}}
    second = {"clip": "b.mp4", "start": 0, "dur": 3,
              "effect": "none", "params": {}}
    glitched = Path(temporary) / "glitched.mp4"
    clean = Path(temporary) / "clean.mp4"
    server.render_timeline([first, second], [{"type": "mosh", "bleed": 10}],
                           str(glitched), 180, "6")
    server.render_timeline([{**first, "effect": "none"}, second],
                           [{"type": "cut"}], str(clean), 180, "6")
    if server.probe(str(glitched), "stream=codec_type", "a") != "audio":
        raise SystemExit("Render check failed: export has no audio track")

    for video, time, name in ((glitched, "1.5", "effect"),
                              (clean, "1.5", "effect_clean"),
                              (glitched, "3.1", "joint"),
                              (clean, "3.1", "joint_clean")):
        run(server.FFMPEG, "-loglevel", "error", "-ss", time, "-i", str(video),
            "-frames:v", "1", "-y", str(Path(temporary) / f"{name}.png"))
    if difference(Path(temporary) / "effect.png",
                  Path(temporary) / "effect_clean.png") < 8:
        raise SystemExit("Render check failed: effect frame looks clean")
    if difference(Path(temporary) / "joint.png",
                  Path(temporary) / "joint_clean.png") < 8:
        raise SystemExit("Render check failed: mosh transition looks clean")

print("Render check passed: effect, mosh transition, and audio")
