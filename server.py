#!/usr/bin/env python3
"""Mosh Studio — local datamosh editor. v2: projects, live timeline proxy.

Playback model: the whole timeline renders to a 480p proxy mp4 with per-segment
caching (only segments whose settings changed re-render). The UI scrubs and
plays that proxy directly — no preview button. Exports render at 1080p.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

BASE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.abspath(os.path.expanduser(os.environ.get("MOSH_CLIPS", "~/Movies/Mosh Studio")))
EXPORTS = os.path.join(CLIPS, "Exports")
CACHE = os.path.join(BASE, "cache")
THUMBS = os.path.join(CACHE, "thumbs")
STRIPS = os.path.join(CACHE, "strips")
PROXIES = os.path.join(CACHE, "proxies")
SEGCACHE = os.path.join(CACHE, "segments")
PROJECTS = os.path.join(BASE, "projects")
STATIC = os.path.join(BASE, "static")
def resolve_ffglitch(name):
    """Use an explicit engine, a local download, or a PATH installation."""
    candidates = []
    if os.environ.get("FFGLITCH_DIR"):
        candidates.append(os.path.join(os.path.expanduser(os.environ["FFGLITCH_DIR"]), name))
    candidates.append(os.path.join(BASE, "ffglitch", name))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


FFGAC = resolve_ffglitch("ffgac")
FFEDIT = resolve_ffglitch("ffedit")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
PORT = int(os.environ.get("MOSH_PORT", "8475"))
FPS = 30


def missing_tool_message():
    messages = []
    if not FFMPEG or not FFPROBE:
        messages.append("ffmpeg and ffprobe are missing. Install them with: brew install ffmpeg")
    if not FFGAC or not FFEDIT:
        messages.append("FFglitch is missing. Download it from https://ffglitch.org/download/ "
                        "and put ffgac and ffedit in this app's ffglitch folder, or set FFGLITCH_DIR.")
    return " ".join(messages)

for d in (CLIPS, EXPORTS, CACHE, THUMBS, STRIPS, PROXIES, SEGCACHE, PROJECTS):
    os.makedirs(d, exist_ok=True)

render_lock = threading.Lock()


def run(cmd, timeout=1800):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-800:] or f"command failed: {cmd[0]}")


def probe(path, entries, streams=None):
    cmd = [FFPROBE, "-v", "error"]
    if streams:
        cmd += ["-select_streams", streams]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", path]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def probe_duration(path):
    try:
        return float(probe(path, "format=duration"))
    except ValueError:
        return 0.0


def safe_clip(name):
    if not re.fullmatch(r"[\w .,()\[\]&@#'+-]+\.(mp4|mov|m4v|avi|mkv|gif)", name, re.I):
        raise ValueError("bad clip name")
    path = os.path.join(CLIPS, name)
    if not os.path.isfile(path):
        raise ValueError(f"clip not found: {name}")
    return path


def convert_movs():
    for f in os.listdir(CLIPS):
        if f.lower().endswith(".mov"):
            src = os.path.join(CLIPS, f)
            out = os.path.join(CLIPS, os.path.splitext(f)[0] + ".mp4")
            if not os.path.exists(out):
                try:
                    run([FFMPEG, "-loglevel", "error", "-i", src, "-vf", "scale=-2:1080",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", out])
                except Exception:
                    pass


def list_clips():
    convert_movs()
    out = []
    for f in sorted(os.listdir(CLIPS)):
        if not f.lower().endswith((".mp4", ".m4v", ".gif", ".avi", ".mkv")):
            continue
        path = os.path.join(CLIPS, f)
        stem = os.path.splitext(f)[0]
        thumb = os.path.join(THUMBS, stem + ".jpg")
        strip = os.path.join(STRIPS, stem + ".jpg")
        if not os.path.exists(thumb) or os.path.getmtime(thumb) < os.path.getmtime(path):
            subprocess.run([FFMPEG, "-loglevel", "quiet", "-ss", "0.5", "-i", path,
                            "-frames:v", "1", "-vf", "scale=320:-2", "-y", thumb])
        if not os.path.exists(strip) or os.path.getmtime(strip) < os.path.getmtime(path):
            dur = max(probe_duration(path), 0.5)
            subprocess.run([FFMPEG, "-loglevel", "quiet", "-i", path,
                            "-vf", f"fps={8/dur:.5f},scale=-2:90,tile=8x1",
                            "-frames:v", "1", "-y", strip])
        out.append({"name": f, "duration": round(probe_duration(path), 2),
                    "thumb": "/thumbs/" + os.path.basename(thumb),
                    "strip": "/strips/" + os.path.basename(strip)})
    return out


# ---------------------------------------------------------------- effects ---

def effect_script(effect, p):
    s = lambda k, d: float(p.get(k, d))
    # FFglitch 0.10 exposes motion vectors as MV2DArray/MVRef objects.
    # A vector can be null even when its MVRef exists, so skip it explicitly.
    each = ("function eachVector(frame, fn) {"
            "const f = frame.mv?.forward; if (!f) return;"
            "f.forEach((v,y,x) => { if (!v.compare_eq(null)) fn(v,y,x,f); });"
            "}\n")
    if effect == "melt":
        thr = s("threshold", 3)
        return each + ("const t=%f; export function glitch_frame(frame) {"
                       "eachVector(frame, v => { if (v.magnitude_sq() > t*t) v.clear(); });"
                       "}\n") % thr
    if effect in ("bloom", "shear"):
        z = s("strength", 20)
        if effect == "shear":
            z = -abs(z)
        return each + ("const Z=%f; export function glitch_frame(frame) {"
                       "eachVector(frame, (v,y,x,f) => {"
                       "v.add(Math.lround((f.width/2-x)*Z/100),"
                       "Math.lround((f.height/2-y)*Z/100));"
                       "});}\n") % z
    if effect == "vibrate":
        rnd = s("strength", 10)
        return each + ("const R=%f; export function glitch_frame(frame) {"
                       "eachVector(frame, v => v.add(Math.floor(Math.random()*R-R/2),"
                       "Math.floor(Math.random()*R-R/2)));}\n") % rnd
    if effect == "freeze":
        chance = s("chance", 5)
        burst = int(s("burst", 10))
        return each + ("const chance=%f, burst=%d; let active=0;"
                       "export function glitch_frame(frame) {"
                       "if (active===0 && Math.random()*100<chance) active=burst;"
                       "if (active>0) { eachVector(frame, v => v.clear()); active--; }"
                       "}\n") % (chance, burst)
    if effect == "gravity":
        chance = s("chance", 2)
        grav = s("strength", 5)
        burst = int(s("burst", 10))
        return each + ("const chance=%f, pull=%f, burst=%d;"
                       "let active=0, trail=[];"
                       "export function glitch_frame(frame) {"
                       "if (active===0 && Math.random()*100<chance) {active=burst;trail=[];}"
                       "if (active>0) {"
                       "eachVector(frame, (v,y,x) => {"
                       "const row=trail[y]||(trail[y]=[]);"
                       "const prior=row[x]===undefined?v[1]:row[x];"
                       "v.assign_v(Math.lround(Math.max(-120,Math.min(120,prior))));"
                       "row[x]=prior+pull;"
                       "}); active--;"
                       "}}\n") % (chance, grav, burst)
    raise ValueError("unknown effect")


# ---------------------------------------------------- cached segment render ---

FORMATS = {"9:16": 9/16, "1:1": 1.0, "16:9": 16/9, "4:5": 4/5}


def target_size(src, height, fmt=None):
    if fmt in FORMATS:
        return int(round(height * FORMATS[fmt] / 2) * 2), height
    vals = [v for v in probe(src, "stream=width,height", "v:0")
            .split("\n")[0].split(",") if v.strip()]
    w, h = int(vals[0]), int(vals[1])
    tw = int(round(w * height / h / 2) * 2)
    return tw, height


def seg_key(seg, tw, th, quality):
    source_stat = os.stat(safe_clip(seg["clip"]))
    spec = json.dumps({"c": seg["clip"], "s": round(float(seg.get("start", 0)), 3),
                       "d": round(float(seg.get("dur", 0)), 3),
                       "e": seg.get("effect") or "none",
                       "p": seg.get("params", {}), "w": tw, "h": th, "q": quality,
                       "sp": seg.get("speed", 1), "vol": seg.get("volume", 1),
                       "adj": seg.get("adjust", {}),
                       "file_size": source_stat.st_size,
                       "file_mtime_ns": source_stat.st_mtime_ns},
                      sort_keys=True)
    return hashlib.md5(spec.encode()).hexdigest()


def render_segment(seg, tw, th, quality):
    """Render one segment to cached raw mpeg2 frames + trimmed audio intermediate
    + waveform strip. Returns (key, rawdir, inter, frame_count)."""
    key = seg_key(seg, tw, th, quality)
    cdir = os.path.join(SEGCACHE, key)
    rawdir = os.path.join(cdir, "raws")
    inter = os.path.join(cdir, "inter.mp4")
    done = os.path.join(cdir, ".done")
    if os.path.exists(done):
        os.utime(cdir)  # touch for LRU pruning
        wave = os.path.join(cdir, "wave.png")
        if not os.path.exists(wave) and probe(inter, "stream=codec_type", "a"):
            subprocess.run([FFMPEG, "-loglevel", "quiet", "-i", inter, "-filter_complex",
                            "aformat=channel_layouts=mono,showwavespic=s=600x44:colors=#49D88B",
                            "-frames:v", "1", "-y", wave])
        return key, rawdir, inter, len(os.listdir(rawdir))
    shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(rawdir)
    src = safe_clip(seg["clip"])
    vf = (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
          f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    adj = seg.get("adjust") or {}
    eq = []
    if float(adj.get("bright", 0)):
        eq.append(f"brightness={max(-0.5, min(0.5, float(adj['bright'])))}")
    if float(adj.get("contrast", 1)) != 1:
        eq.append(f"contrast={max(0.2, min(2.5, float(adj['contrast'])))}")
    if float(adj.get("sat", 1)) != 1:
        eq.append(f"saturation={max(0, min(3, float(adj['sat'])))}")
    if eq:
        vf += ",eq=" + ":".join(eq)
    if float(adj.get("hue", 0)):
        vf += f",hue=h={max(-180, min(180, float(adj['hue'])))}"
    speed = max(0.25, min(4.0, float(seg.get("speed", 1) or 1)))
    if speed != 1:
        vf += f",setpts=PTS/{speed}"
    af = []
    sp = speed
    while sp > 2.0:
        af.append("atempo=2.0"); sp /= 2.0
    while sp < 0.5:
        af.append("atempo=0.5"); sp /= 0.5
    if abs(sp - 1) > 1e-6:
        af.append(f"atempo={sp:.4f}")
    vol = max(0, min(2, float(seg.get("volume", 1) if seg.get("volume") is not None else 1)))
    if vol != 1:
        af.append(f"volume={vol}")
    trim = ["-ss", str(float(seg.get("start", 0)))]
    if seg.get("dur"):
        trim += ["-t", str(float(seg["dur"]))]
    cmd = [FFMPEG, "-loglevel", "error", *trim, "-i", src, "-vf", vf, "-r", str(FPS)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-y", inter]
    run(cmd)
    effect = seg.get("effect") or "none"
    flags = "+nopimb+forcemv" if effect != "none" else "+nopimb"
    mpg = os.path.join(cdir, "seg.mpg")
    run([FFGAC, "-loglevel", "error", "-i", inter, "-an", "-mpv_flags", flags,
         "-qscale:v", quality, "-vcodec", "mpeg2video", "-f", "rawvideo",
         "-g", "1000", "-y", mpg])
    if effect != "none":
        script = os.path.join(cdir, "fx.js")
        with open(script, "w") as fp:
            fp.write(effect_script(effect, seg.get("params", {})))
        moshed = os.path.join(cdir, "moshed.mpg")
        run([FFEDIT, "-loglevel", "error", "-i", mpg, "-f", "mv", "-s", script, "-o", moshed])
        mpg = moshed
    run([FFGAC, "-loglevel", "error", "-i", mpg, "-vcodec", "copy",
         os.path.join(rawdir, "f_%05d.raw")])
    for f in ("seg.mpg", "moshed.mpg"):
        try:
            os.remove(os.path.join(cdir, f))
        except OSError:
            pass
    if probe(inter, "stream=codec_type", "a"):
        subprocess.run([FFMPEG, "-loglevel", "quiet", "-i", inter, "-filter_complex",
                        "aformat=channel_layouts=mono,showwavespic=s=600x44:colors=#49D88B",
                        "-frames:v", "1", "-y", os.path.join(cdir, "wave.png")])
    open(done, "w").close()
    return key, rawdir, inter, len(os.listdir(rawdir))


def prune_segcache(keep=120):
    dirs = [os.path.join(SEGCACHE, d) for d in os.listdir(SEGCACHE)]
    dirs = [d for d in dirs if os.path.isdir(d)]
    if len(dirs) <= keep:
        return
    dirs.sort(key=os.path.getmtime)
    for d in dirs[:len(dirs) - keep]:
        shutil.rmtree(d, ignore_errors=True)


def render_timeline(segments, joints, out_mp4, height, quality, overlays=None,
                    texts=None, fmt=None):
    """Concat cached segments, dropping keyframes at mosh joints; bake + audio;
    composite overlay (PiP) clips on top. Returns (starts, total, seg_keys)."""
    if not segments:
        raise ValueError("timeline is empty")
    with render_lock:
        tw, th = target_size(safe_clip(segments[0]["clip"]), height, fmt)
        rendered = [render_segment(s, tw, th, quality) for s in segments]
        skips = [0]
        for i in range(1, len(segments)):
            joint = joints[i - 1] if i - 1 < len(joints) else {}
            if joint.get("type", "mosh") == "mosh":
                avail = rendered[i][3]
                skips.append(max(1, min(int(joint.get("bleed", 5)), avail - 5)))
            else:
                skips.append(0)
        starts, t = [], 0.0
        work = os.path.join(CACHE, "work")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        combined = os.path.join(work, "combined.mpg")
        with open(combined, "wb") as out:
            for i, (_key, rawdir, _inter, count) in enumerate(rendered):
                starts.append(round(t, 3))
                frames = sorted(os.listdir(rawdir))[skips[i]:]
                t += len(frames) / FPS
                for fr in frames:
                    with open(os.path.join(rawdir, fr), "rb") as fp:
                        out.write(fp.read())
        inters = [r[2] for r in rendered]
        bake = [FFMPEG, "-loglevel", "error", "-i", combined]
        for _key, _rawdir, inter, count in rendered:
            if probe(inter, "stream=codec_type", "a"):
                bake += ["-i", inter]
            else:
                bake += ["-f", "lavfi", "-t", f"{count/FPS:.4f}",
                         "-i", "anullsrc=r=48000:cl=stereo"]
        parts, labels = [], []
        for i in range(len(inters)):
            parts.append(f"[{i+1}:a]atrim=start={skips[i]/FPS:.4f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
        fc = ";".join(parts) + f";{''.join(labels)}concat=n={len(inters)}:v=0:a=1[aout]"
        bake += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:a", "aac"]
        gop = ["-g", "15"] if height <= 480 else []
        baked = os.path.join(work, "baked.mp4") if (overlays or texts) else out_mp4
        bake += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", *gop,
                 "-pix_fmt", "yuv420p", "-shortest", "-y", baked]
        run(bake)
        if overlays or texts:
            composite_overlays(baked, overlays or [], out_mp4, tw, th, gop,
                               texts or [], work)
        prune_segcache()
        return starts, round(t, 3), [r[0] for r in rendered]


POS = {"tl": ("W*0.04", "H*0.04"), "tr": ("W-w-W*0.04", "H*0.04"),
       "bl": ("W*0.04", "H-h-H*0.04"), "br": ("W-w-W*0.04", "H-h-H*0.04"),
       "c": ("(W-w)/2", "(H-h)/2")}


FONT = "/System/Library/Fonts/Helvetica.ttc"


def text_png(content, size_px, color, path):
    """Render a text block to a transparent PNG (ffmpeg here has no drawtext)."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(FONT, size_px)
    except OSError:
        font = ImageFont.load_default()
    lines = content.split("\n")
    probe_img = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(probe_img)
    widths = [d.textlength(ln, font=font) for ln in lines]
    lh = int(size_px * 1.25)
    w = max(8, int(max(widths)) + 8)
    h = lh * len(lines) + 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        x = (w - widths[i]) / 2
        d.text((x + 2, i * lh + 2), ln, font=font, fill=(0, 0, 0, 160))
        d.text((x, i * lh), ln, font=font, fill=color)
    img.save(path)
    return w, h


def composite_overlays(base, overlays, out_mp4, tw, th, gop, texts=(), work=None):
    """Draw PiP overlay clips (muted, no mosh) and text titles on the timeline."""
    cmd = [FFMPEG, "-loglevel", "error", "-i", base]
    fc, prev = [], "0:v"
    for i, ov in enumerate(overlays):
        src = safe_clip(ov["clip"])
        cmd += ["-ss", str(float(ov.get("start", 0))), "-t", str(float(ov.get("dur", 3))),
                "-i", src]
        scale = min(max(float(ov.get("scale", 0.4)), 0.15), 1.0)
        x, y = POS.get(ov.get("pos", "br"), POS["br"])
        at = float(ov.get("at", 0))
        fc.append(
            f"[{i+1}:v]scale={int(tw*scale/2)*2}:-2,setpts=PTS-STARTPTS+{at:.3f}/TB[ov{i}];"
            f"[{prev}][ov{i}]overlay=x={x}:y={y}:"
            f"enable='between(t,{at:.3f},{at + float(ov.get('dur', 3)):.3f})'[v{i}]")
        prev = f"v{i}"
    n_ov = len(overlays)
    tpos = {"tl": ("W*0.05", "H*0.06"), "tr": ("W-w-W*0.05", "H*0.06"),
            "bl": ("W*0.05", "H-h-H*0.06"), "br": ("W-w-W*0.05", "H-h-H*0.06"),
            "c": ("(W-w)/2", "(H-h)/2")}
    k_used = 0
    for k, tx in enumerate(texts):
        content = str(tx.get("text", ""))[:300]
        if not content.strip():
            continue
        size = int(min(max(float(tx.get("size", 0.06)), 0.02), 0.2) * th)
        color = re.sub(r"[^#0-9a-zA-Z]", "", str(tx.get("color", "white"))) or "white"
        png = os.path.join(work, f"text{k}.png")
        text_png(content, size, color, png)
        cmd += ["-loop", "1", "-i", png]
        x, y = tpos.get(tx.get("pos", "c"), tpos["c"])
        at = float(tx.get("at", 0))
        end = at + float(tx.get("dur", 3))
        idx = 1 + n_ov + k_used
        k_used += 1
        fc.append(
            f"[{prev}][{idx}:v]overlay=x={x}:y={y}:"
            f"enable='between(t,{at:.3f},{end:.3f})'[t{k}]")
        prev = f"t{k}"
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{prev}]", "-map", "0:a?",
            "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            *gop, "-pix_fmt", "yuv420p", "-shortest", "-y", out_mp4]
    run(cmd)


# ---------------------------------------------------------------- projects ---

def sanitize_name(name):
    return re.sub(r"[^\w .,()&'+-]", "", name).strip() or "Untitled"


def project_path(pid):
    if not re.fullmatch(r"[0-9a-f]{12}", pid):
        raise ValueError("bad project id")
    return os.path.join(PROJECTS, pid + ".json")


def list_projects():
    out = []
    for f in os.listdir(PROJECTS):
        if f.endswith(".json"):
            try:
                p = json.load(open(os.path.join(PROJECTS, f)))
                segs = p.get("segments", [])
                thumb = None
                if segs:
                    stem = os.path.splitext(segs[0]["clip"])[0]
                    if os.path.exists(os.path.join(THUMBS, stem + ".jpg")):
                        thumb = "/thumbs/" + stem + ".jpg"
                out.append({"id": p["id"], "name": p.get("name", "Untitled"),
                            "modified": p.get("modified", 0), "thumb": thumb,
                            "clips": len(segs),
                            "duration": round(sum(float(s.get("dur", 0)) for s in segs), 1)})
            except Exception:
                continue
    out.sort(key=lambda x: -x["modified"])
    return out


def export_name(stem):
    stem = sanitize_name(stem).replace(" ", "_")
    n, out = 0, os.path.join(EXPORTS, f"{stem}.mp4")
    while os.path.exists(out):
        n += 1
        out = os.path.join(EXPORTS, f"{stem}({n}).mp4")
    return out


# ----------------------------------------------------------------- server ---

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _valid_request(self, mutating=False):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        supplied = self.headers.get_all("Host") or []
        if len(supplied) != 1 or supplied[0].lower() not in hosts:
            self.close_connection = True
            self.send_error(403, "Use localhost or 127.0.0.1 to open Mosh Studio")
            return False
        if mutating:
            origins = self.headers.get_all("Origin") or []
            allowed_origin = f"http://{supplied[0].lower()}"
            site = self.headers.get("Sec-Fetch-Site")
            if ((origins and (len(origins) != 1 or origins[0].lower() != allowed_origin))
                    or (site and site not in ("same-origin", "none"))):
                self.close_connection = True
                self.send_error(403, "This request must come from Mosh Studio")
                return False
        return True

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, download=False):
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404)
            return
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if download:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{os.path.basename(path)}"')
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as fp:
            fp.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fp.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    return
                remaining -= len(chunk)

    def do_GET(self):
        if not self._valid_request():
            return
        path, _, query = self.path.partition("?")
        if path == "/":
            self._file(os.path.join(STATIC, "index.html"), "text/html")
        elif path == "/api/status":
            message = missing_tool_message()
            self._json({"ready": not bool(message), "message": message})
        elif path == "/api/clips":
            message = missing_tool_message()
            if message:
                self._json({"error": message}, 503)
            else:
                self._json(list_clips())
        elif path == "/api/projects":
            self._json(list_projects())
        elif path.startswith("/api/project/"):
            try:
                self._json(json.load(open(project_path(path.rsplit("/", 1)[1]))))
            except (ValueError, OSError):
                self.send_error(404)
        elif path.startswith("/thumbs/"):
            self._file(os.path.join(THUMBS, os.path.basename(unquote(path))), "image/jpeg")
        elif path.startswith("/strips/"):
            self._file(os.path.join(STRIPS, os.path.basename(unquote(path))), "image/jpeg")
        elif path.startswith("/media/"):
            try:
                self._file(safe_clip(unquote(os.path.basename(path))), "video/mp4")
            except ValueError:
                self.send_error(404)
        elif path.startswith("/segwave/"):
            k = os.path.basename(path)[:-4]
            if re.fullmatch(r"[0-9a-f]{32}", k):
                self._file(os.path.join(SEGCACHE, k, "wave.png"), "image/png")
            else:
                self.send_error(404)
        elif path.startswith("/proxies/"):
            self._file(os.path.join(PROXIES, os.path.basename(path)), "video/mp4")
        elif path.startswith("/exports/"):
            self._file(os.path.join(EXPORTS, os.path.basename(unquote(path))),
                       "video/mp4", download=("dl=1" in query))
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._valid_request(mutating=True):
            return
        if self.path.startswith("/api/project/"):
            try:
                os.remove(project_path(self.path.rsplit("/", 1)[1]))
                self._json({"ok": True})
            except (ValueError, OSError):
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._valid_request(mutating=True):
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            if self.path in ("/api/upload", "/api/proxy", "/api/export"):
                message = missing_tool_message()
                if message:
                    self.close_connection = True
                    self._json({"error": message}, 503)
                    return
            if self.path == "/api/upload":
                name = os.path.basename(unquote(self.headers.get("X-Filename", "upload.mp4")))
                name = re.sub(r"[^\w .,()\[\]&@#'+-]", "_", name)
                if not re.search(r"\.(mp4|mov|m4v|avi|mkv|gif)$", name, re.I):
                    raise ValueError("that file type isn't a video I can use")
                if n > 3_000_000_000:
                    raise ValueError("file too large")
                dest = os.path.join(CLIPS, name)
                stem, ext = os.path.splitext(dest)
                k = 0
                while os.path.exists(dest):
                    k += 1
                    dest = f"{stem}({k}){ext}"
                with open(dest, "wb") as fp:
                    remaining = n
                    while remaining > 0:
                        chunk = self.rfile.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        fp.write(chunk)
                        remaining -= len(chunk)
                convert_movs()
                self._json({"ok": True, "name": os.path.basename(dest)})
                return
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/project":
                pid = req.get("id") or uuid.uuid4().hex[:12]
                proj = {"id": pid, "name": sanitize_name(req.get("name", "Untitled")),
                        "segments": req.get("segments", []), "joints": req.get("joints", []),
                        "overlays": req.get("overlays", []),
                        "texts": req.get("texts", []),
                        "format": req.get("format"),
                        "modified": time.time(),
                        "created": req.get("created") or time.time()}
                with open(project_path(pid), "w") as fp:
                    json.dump(proj, fp)
                self._json({"ok": True, "id": pid})
            elif self.path == "/api/proxy":
                out = os.path.join(PROXIES, f"proxy_{int(time.time()*1000)}.mp4")
                starts, total, keys = render_timeline(req["segments"], req.get("joints", []),
                                                      out, 480, "6",
                                                      overlays=req.get("overlays"),
                                                      texts=req.get("texts"),
                                                      fmt=req.get("format"))
                for old in os.listdir(PROXIES):
                    if os.path.join(PROXIES, old) != out:
                        try:
                            os.remove(os.path.join(PROXIES, old))
                        except OSError:
                            pass
                waves = []
                for k in keys:
                    w = os.path.join(SEGCACHE, k, "wave.png")
                    waves.append("/segwave/" + k + ".png" if os.path.exists(w) else None)
                self._json({"url": "/proxies/" + os.path.basename(out),
                            "starts": starts, "total": total, "waves": waves})
            elif self.path == "/api/export":
                out = export_name(req.get("name", "mosh"))
                render_timeline(req["segments"], req.get("joints", []), out, 1080, "2",
                                overlays=req.get("overlays"), texts=req.get("texts"),
                                fmt=req.get("format"))
                self._json({"file": os.path.basename(out),
                            "url": "/exports/" + os.path.basename(out)})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


if __name__ == "__main__":
    message = missing_tool_message()
    if message:
        print(message, file=sys.stderr, flush=True)
    else:
        threading.Thread(target=convert_movs, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
