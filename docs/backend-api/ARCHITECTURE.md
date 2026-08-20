# Reel Studio — Architecture & Developer Guide

> One-clip-in, post-ready-vertical-reel-out. A user uploads a talking-head video; the system
> transcribes it, auto-edits it (trim silences, clean audio, Hinglish word-by-word captions,
> stock B-roll on key moments, emphasis auto-zoom, colour grade), and returns a 1080×1920 reel.

---

## 1. System overview

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                     ONE MACHINE                           │
   Phone / Browser  │                                                          │
   ┌────────────┐   │   ┌─────────────────┐        ┌────────────────────────┐  │
   │  Mobile    │   │   │  Vite dev :5173 │        │  FastAPI (uvicorn)      │  │
   │  web app   │──HTTP─▶│  serves the SPA │        │  server.py  :8000       │  │
   │ (React/TS) │   │   │  proxies /api ──┼──HTTP─▶│  (SINGLE worker)        │  │
   └────────────┘   │   └─────────────────┘        │                        │  │
        ▲           │                              │  • per-project state    │  │
        │  SSE      │                              │  • job queue (semaphore)│  │
        └───────────┼──────────────────────────────┤  • SSE progress         │  │
                    │                              └────────┬───────────────┘  │
                    │                                       │ spawns subprocess │
                    │                              ┌────────▼───────────────┐  │
                    │                              │ pipeline_runner.py      │  │
                    │                              │ (VEX venv Python)       │  │
                    │                              │  → reel-studio/pipeline │  │
                    │                              │  ffmpeg · Pillow · LLM  │  │
                    │                              └────────┬───────────────┘  │
                    └───────────────────────────────────────┼──────────────────┘
                                                             │ HTTPS
                                          ┌──────────────────▼───────────────────┐
                                          │ External APIs (shared keys)           │
                                          │ ElevenLabs STT · OpenAI · Pexels/Pixabay │
                                          └───────────────────────────────────────┘
```

**Three tiers, one box:**
1. **Frontend** — a React + TypeScript single-page app (mobile-first). Talks only to `/api/*`.
2. **Backend** — a FastAPI app (`server.py`) on uvicorn. Holds all session state **in memory** and orchestrates jobs. **Runs as a single worker** (see [CONCURRENCY.md](./CONCURRENCY.md)).
3. **Pipeline** — the heavy editing work runs in a **spawned subprocess** (`pipeline_runner.py`, executed with a separate "VEX" virtualenv) that imports the shared `reel-studio/pipeline` module and shells out to `ffmpeg`/`Pillow`, calling external APIs for transcription, planning, and stock media.

---

## 2. Repo layout (key files)

```
video-edit-mobile/
├── reel-studio-ui/                     # the app
│   ├── src/
│   │   ├── mobile/                     # ← the mobile app (what users run)
│   │   │   ├── MobileApp.tsx           #   the whole flow: state machine + screens
│   │   │   ├── api.ts                  #   backend client (fetch + SSE)
│   │   │   ├── theme.css               #   Reel Editor brand tokens + components
│   │   │   ├── main.tsx                #   mobile entry (mounts #mobile-root)
│   │   │   └── assets/                 #   logo SVGs
│   │   ├── App.tsx + components/       #   the desktop editor (separate consumer)
│   │   └── ...
│   ├── backend/
│   │   ├── server.py                   # ← FastAPI: routes, jobs, per-project store, SSE
│   │   ├── pipeline_runner.py          #   subprocess entry: ingest/recaption/regenerate/render
│   │   ├── mobile_render/
│   │   │   └── render_broll_ffmpeg.py  #   pure-ffmpeg bake (the mobile render path)
│   │   ├── broll_plan.py               #   LLM: pick B-roll moments
│   │   ├── zoom_plan.py                #   LLM + audio energy: pick zoom moments
│   │   ├── broll_fetch.py              #   Pexels/Pixabay stock search
│   │   ├── caption_styles.py           #   the ONE caption-style registry
│   │   └── .venv/                      #   backend venv (fastapi/uvicorn) — Python 3.9
│   └── docs/                           # ← you are here
├── reel-studio/pipeline.py             #   shared pipeline lib (transcribe→grade→caption→export)
└── vex/                                #   VEX venv + .env (API keys) used by the subprocess
```

---

## 3. The user flow → what the backend does

| Screen | User action | Backend |
|---|---|---|
| **Create** | pick/drop a video | `POST /api/upload` → mints `project_id`, starts an **ingest** job |
| **Processing** | waits | ingest subprocess: transcribe → romanize → grade → upscale (captions OFF). Progress streamed over SSE |
| **Setup** | toggles + caption style | reads `GET /api/caption-styles`; toggles map to upload params + the run recipe |
| **Processing** | "Start editing" | `broll/plan` → `zoom/plan` → (per scene) `broll/fetch` → `broll/cache` → `broll/render` |
| **Ready** | preview / download | `<video>` ← `GET /api/result/{name}`; meta from `GET /api/output-meta` |
| **Fine-tune** | swap clips / edit words / re-toggle | re-bakes via `broll/render` over the **same ingested base** |
| **Saved** | confirmation | client-side transition (no backend) |

Full request/response detail: **[API.md](./API.md)**.

---

## 4. The editing pipeline (what actually makes the reel)

The pipeline is two halves:

**Front half (ingest, on upload)** — `reel-studio/pipeline.run_pipeline(captions=False)`:
`clean audio → transcribe (ElevenLabs) → romanize Hinglish (LLM) → trim silences → colour grade → upscale to 1080×1920`. Produces the **base video** + a **transcript** (word-level timings). This is the reusable base for all later edits.

**Back half (bake, on render)** — `mobile_render/render_broll_ffmpeg.py` (pure ffmpeg, no headless browser):
`base + accepted B-roll overlays + auto-zoom punch-ins + burned captions → loudnorm to −14 LUFS → QC`. Produces the final reel in `videos/reel-studio-out/`.

Re-rendering (Fine-tune) reuses the ingested base, so it's fast.

---

## 5. Tech stack

| Layer | Tech |
|---|---|
| Frontend | React 18 + TypeScript, Vite, no UI framework (self-contained `theme.css`), lucide-react icons |
| Backend | FastAPI + uvicorn (**Python 3.9**), async, SSE for progress |
| Media | ffmpeg / ffprobe, Pillow (graphic cards), two-pass loudnorm |
| AI/ML | ElevenLabs Scribe (speech-to-text), OpenAI (moment/zoom planning, romanization) |
| Stock | Pexels + Pixabay (SSRF-guarded download cache) |

---

## 6. Run guide (local dev)

**Prerequisites:** `ffmpeg`/`ffprobe` on PATH; the backend venv (`backend/.venv`) with fastapi/uvicorn; the VEX venv (`vex/.venv`) that can import the pipeline; API keys in `vex/.env`.

```bash
# 1) Backend (single worker — REQUIRED; loads API keys for the pipeline subprocess)
cd video-edit-mobile/reel-studio-ui/backend
set -a; . ../../vex/.env; set +a
./.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

# 2) Frontend (Vite; proxies /api → :8000)
cd video-edit-mobile/reel-studio-ui
npm run dev -- --host
```

- App: **http://localhost:5173/mobile.html** (desktop) or **http://<lan-ip>:5173/mobile.html** (phone, same Wi-Fi).
- The backend does **not** auto-reload — restart it after editing `server.py` / the pipeline.
- Dev-only: `?dev=<screen>` jumps straight to a screen (gated by `import.meta.env.DEV`).

**Environment knobs** (devops tunes per machine): `MAX_CONCURRENT_JOBS` (default 2), `MAX_UPLOAD_MB` (500), `JOB_TTL_MIN` (15), `FILE_TTL_MIN` (120). See [CONCURRENCY.md](./CONCURRENCY.md).

---

## 7. Where to change things (guide)

| I want to… | Edit |
|---|---|
| Change a screen's UI / flow | `src/mobile/MobileApp.tsx` |
| Change brand colours / spacing | `src/mobile/theme.css` (all `--su-*` tokens) |
| Add/change a backend endpoint | `backend/server.py` |
| Change caption styles | `backend/caption_styles.py` (the one registry) |
| Change how B-roll moments are chosen | `backend/broll_plan.py` |
| Change zoom selection | `backend/zoom_plan.py` |
| Change the final bake (overlays/captions/loudnorm) | `backend/mobile_render/render_broll_ffmpeg.py` |
| Change transcription/grade/upscale (front half) | `reel-studio/pipeline.py` (shared — keep changes additive) |
| Tune concurrency/limits | env vars (above) |

---

## 8. Scope & non-goals (current)

**In scope (built):** the full reel pipeline — upload → ingest → plan → render → result, with captions/B-roll/zoom/grade, per-project isolation, and progress streaming.

**Out of scope (deliberately not built):** Home content feed, saved-library browsing, "describe your reel" prompt-to-reel, **auth/user accounts**, **billing/paywall**, **share links**. The app opens directly on the upload screen. Adding any of these is net-new work (auth would be the foundation the others depend on).

**Hard constraint:** single uvicorn worker (in-memory state). See [CONCURRENCY.md](./CONCURRENCY.md) §"When you need Redis".
