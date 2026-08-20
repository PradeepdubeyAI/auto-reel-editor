# mobile_render

Remotion-free bake for the **production mobile app** (`/api/broll/render?engine=ffmpeg`).

- `render_broll_ffmpeg.py` — composites base + B-roll + Ken Burns + graphic cards + auto-zoom +
  word-by-word captions using **pure ffmpeg + Pillow** (no headless Chrome, no Remotion license).
- Imports the SHARED finishing from `reel-studio/pipeline.py` (`_pro_export` + 2-pass loudnorm +
  `_qc`) — identical encode/QC to the normal reel. That import uses an absolute path, so this file
  is location-independent.
- The DESKTOP web app is unchanged and still uses `../render_broll.py` (Remotion) for its live
  preview==export path. Shared code (pipeline.py, server.py, pipeline_runner.py) is NOT here.
