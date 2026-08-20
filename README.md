# Reel Studio

A mobile-first video editor for turning a single raw talking-head clip into a
graded, captioned, sound-designed vertical reel. Upload a video, the pipeline
transcribes it and applies a first pass (silence removal, retake cleanup, hook
trim, color grade, word-by-word captions); the mobile UI then lets you review
and adjust every step — trims, zooms, b-roll, music, SFX, graphics — before
export.

## How it's built

Three pieces:

```
frontend/    React + TypeScript mobile web app (Vite)      -> reel-studio-ui
backend/     FastAPI server: uploads, jobs, project state    -> talks to...
pipeline/    the actual video pipeline (transcribe, cut,     <- spawned as a
             grade, caption, export)                            subprocess
```

The backend never imports the pipeline directly — it spawns `pipeline_runner.py`
as a subprocess in its own Python environment (see Setup) and streams back
NDJSON progress events, which is why upload/edit/export can run without
blocking the API server.

`pipeline/vendor/` is a trimmed, vendored copy of the underlying video-engine
library this pipeline is built on (transcription, silence detection, color
grading, subtitle rendering, project-state management) — just the ~25 modules
this app actually calls, not the full framework.

### The mobile flow

Six screens (`frontend/src/mobile/MobileApp.tsx`): **create → setup →
processing → ready → finetune → saved**. After the first automated pass,
`finetune` is where the manual edit surface lives — a shared video-player +
waveform + timeline component (`EditSurface`) used consistently across trim,
retake, zoom, and sound-effect editing, so every toggle in the app previews
against the same real timeline instead of a static list.

## Setup

Requirements: Python 3.11+, Node 18+, `ffmpeg`/`ffprobe` on `PATH`.

```bash
# backend + pipeline
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# frontend
cd frontend && npm install
```

Copy `.env.example` to `.env` in the repo root and fill in the keys you have
(see the file for what each one unlocks — the app degrades gracefully per
missing key, e.g. skip `SARVAM_API_KEY` if you only transcribe with
ElevenLabs).

Run both halves:

```bash
# terminal 1 -- backend API (port 8000)
cd backend && ../.venv/bin/python3 -m uvicorn server:app --reload

# terminal 2 -- mobile frontend (port 5173)
cd frontend && npm run dev
```

Open the frontend URL on a phone or in a mobile-width browser window.

### Captions: pycaps needs its own venv

Word-by-word caption rendering (`pycaps`) drives a headless browser renderer
that conflicts with the packages in `requirements.txt`, so it must live in a
**separate** virtualenv:

```bash
python3 -m venv .pycaps-venv
./.pycaps-venv/bin/pip install pycaps
```

The pipeline finds it at `<repo root>/.pycaps-venv/bin/pycaps` by default, or
via the `PYCAPS_BIN` env var if you put it elsewhere.

## What the pipeline does

`pipeline/pipeline.py`'s automated first pass, in order:

1. **Transcribe** — ElevenLabs Scribe or Sarvam (word-level timestamps), with
   optional Hinglish romanization via an LLM call
2. **Hook trim** — drops slow preamble so the reel opens on its strongest line
3. **Remove silences** — speech-relative gating, not a fixed noise floor
4. **Remove retakes** — detects abandoned/flubbed lines and cuts them,
   keeping the good take (see `docs/RETAKE_*.md` for the detection approach
   and its known failure modes)
5. **Color grade** — shot-aware, subtle by default (`auto`/`natural`), or one
   of the presets in `pipeline/vendor/color_grading.py`
6. **Captions** — pycaps word-by-word burn-in
7. **Export** — 1080×1920, loudness-normalized audio, QC pass

From there the mobile UI's `finetune` screen exposes trims, zooms, b-roll,
music, SFX and captions as individually toggleable, individually undoable
edits on top of that first pass.

## Known limitations

- **Retake detection is tuned, not solved.** See `docs/RETAKE_HANDOFF.md` and
  `docs/RETAKE_BEST_CONFIG_AND_V1_RESULT.md` — the current config beats the
  shipped baseline on a 10-script held-out-ish eval set, but every number
  there is still in-sample relative to how the filters were chosen. Treat it
  as "measurably better," not "correct."
- **Single-worker backend.** The job runner assumes one video processes at a
  time per project; concurrent multi-user use isn't load-tested.
- **English + Hindi/Hinglish only.** Sentence-boundary detection, retake
  markers, and tokenization are tuned for Latin and Devanagari script; other
  scripts (CJK, Arabic, etc.) will misbehave — see the audit notes in
  `docs/RETAKE_HANDOFF.md`.
- **B-roll fetch needs Pexels/Pixabay keys**; without them that feature is a
  no-op rather than a hard failure.

## Docs

`docs/` carries the internal design and research notes this project was built
from — architecture decisions, retake-detection experiments and their
results, and the eval harness (`docs/eval/`) used to measure them.
