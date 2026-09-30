# Architecture

Mosh Studio is a local Python HTTP server (`server.py`) with a single-page
browser interface (`static/index.html`). It binds to `127.0.0.1` only. Project
files live in `projects/`; generated previews and segment renders live in
`cache/`. Those folders are not part of the source distribution.

## Tool and file locations

- `ffmpeg` and `ffprobe` are found on `PATH`.
- `ffgac` and `ffedit` come from `FFGLITCH_DIR`, then `ffglitch/` beside
  `server.py`, then `PATH`. FFglitch binaries are not included in the repo.
- `MOSH_CLIPS` chooses the clips folder; the default is `~/Movies/Mosh Studio`.
  Exports go in its `Exports/` subfolder.
- `MOSH_PORT` can override the default local port, 8475. `run.sh` picks a free
  local port when no override is set.

## Render pipeline

For each timeline segment, `ffmpeg` trims, scales, pads, and applies basic
adjustments. FFglitch's `ffgac` encodes an MPEG-2 stream with motion vectors.
When an effect is selected, `ffedit` runs a generated JavaScript module over
those vectors. The six effects are Melt, Bloom, Shear, Vibrate, Freeze, and
Gravity; None leaves the segment clean.

The server splits the MPEG-2 stream into frames and joins segment frames in
timeline order. A mosh joint drops the incoming segment's opening frames, so
its motion draws over the previous segment's image. A hard cut keeps the
opening frame. `ffmpeg` then encodes the joined video as H.264 and joins audio
from each segment, generating silence for clips without audio. Text and overlay
clips are composited in a final pass.

Previews use 480p; exports use 1080p. Segment cache keys include the clip
name, file size and modification time, trim, effect, adjustments, dimensions,
and render quality. A render lock protects the shared work directory.

The server rejects unexpected Host headers and cross-origin write requests.
It serves no files outside its known clip, project, cache, and static paths.
