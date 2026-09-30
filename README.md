# Mosh Studio

Mosh Studio is a local video editor for datamoshing. Arrange clips on a
timeline, add motion-vector glitch effects and mosh transitions, preview the
result, and export a 1080p MP4. Your clips and projects stay on your computer.

**Tested setup:** macOS on Apple Silicon. Intel Macs and Linux have not been
tested.

## Install and start

1. Download this repository as a ZIP and unzip it. Keep the whole Mosh Studio
   folder together.
2. Install [Homebrew](https://brew.sh/) if you do not have it. Open Terminal and
   run `brew install python ffmpeg`.
3. Download the [FFglitch 0.10.2 Apple Silicon ZIP](https://ffglitch.org/pub/bin/macos-aarch64/ffglitch-0.10.2-macos-aarch64.zip)
   from [FFglitch's download page](https://ffglitch.org/download/) and unzip it.
   Make a folder named `ffglitch` inside the Mosh Studio folder. Put the
   **`ffgac` and `ffedit` files directly inside** that folder. These separate
   programs are not included with Mosh Studio.
4. Open Terminal in the Mosh Studio folder. One way is to type `cd ` (with a
   space), drag the folder from Finder into Terminal, and press Return. Then run:

   ```sh
   ./run.sh
   ```

The first run sets up the Python package it needs. Your browser opens the
editor at a local address, usually `http://127.0.0.1:8475/`. Keep the Terminal
window open while using Mosh Studio; press Control-C there to stop it. If
another app uses that port, Mosh Studio chooses the next free one.

## Make a video

1. Create a project, then use the upload button in **Media** to import videos.
2. Drag clips onto the timeline to arrange them. Select a clip to trim it or
   change its speed, volume, and look.
3. Select a clip and choose an effect in **Effects**. Select the joint between
   clips to choose a hard cut or a mosh transition. You can also add text and
   picture-in-picture clips.
4. Press **Export**. The MP4 downloads in your browser and is saved in the
   `Exports` folder inside your clips folder.

By default, clips are stored in `~/Movies/Mosh Studio` and projects are stored
in this application's `projects` folder. To use another clips folder, start
the app with `MOSH_CLIPS=/path/to/your/clips ./run.sh`. To use FFglitch from
another location, start it with `FFGLITCH_DIR=/path/to/unzipped/FFglitch ./run.sh`.

## If something is missing

- **“ffmpeg and ffprobe are missing”**: run `brew install ffmpeg`, then restart
  Mosh Studio.
- **“FFglitch is missing”**: check that both `ffgac` and `ffedit` are directly
  inside the `ffglitch` folder, or set `FFGLITCH_DIR` to the folder containing
  them. Download the Apple Silicon version from
  [FFglitch](https://ffglitch.org/download/). If Terminal says a file is not
  executable, run `chmod +x ffglitch/ffgac ffglitch/ffedit` in the Mosh Studio
  folder.
- **“Python 3 is missing”**: run `brew install python` and try `./run.sh` again.
- **The browser did not open**: use the local address printed in Terminal.

For a quick rendering check after setup, run `.venv/bin/python check.py` from
the Mosh Studio folder.

## Credits and license

Mosh Studio is MIT licensed; see [LICENSE](LICENSE). It calls
[FFglitch](https://ffglitch.org/) by Ramiro Polla and
[FFmpeg](https://ffmpeg.org/) as separately installed tools. Some effect ideas
and scripts were adapted from [Datamosher Pro](https://github.com/Akascape/Datamosher-Pro)
by Akash Bora. See [third-party notices](THIRD_PARTY_NOTICES.md) for details and
the separate licenses.
