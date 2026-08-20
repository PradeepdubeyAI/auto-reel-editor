"""Reel Editor backend.

Serves the ACTIVE project (a fixture by default; an uploaded+ingested clip after upload):
  - GET  /api/project       -> {name, videoUrl, fps, width, height, durationMs, words[]}
  - GET  /api/video         -> active base mp4 via FileResponse (HTTP Range -> scrubbing)
  - GET  /api/transcript    -> active whisper_json

Uploads + pipeline runs (spawns pipeline_runner.py with the VEX venv Python; relays NDJSON
over SSE):
  - POST /api/upload        -> save raw video body, ingest it (front half of the pipeline,
                               captions OFF), then make it the active project
  - POST /api/recaption     -> fast captions-only path on the active base
  - POST /api/regenerate    -> full pipeline on the active source
  - GET  /api/progress/{id} -> SSE progress stream
  - GET  /api/result/{name} -> Range-capable produced output

The backend NEVER imports the pipeline; real paths live server-side.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import caption_styles  # the ONE caption-style registry (served to the mobile frontend)
import db  # narrow SQLite persistence (asset libraries + prefs only — see db.py docstring)
import sfx_catalog  # the ONE SFX registry (promoted static assets — see sfx_catalog.py docstring)
import music_catalog  # the ONE music registry (promoted CC0 tracks — see music_catalog.py docstring)

ROOT = Path(__file__).resolve().parent.parent

# --- default fixture project (fallback) ---
FIXTURE = {
    "name": "vex-sarvam — sample reel",
    "video": ROOT / "remotion-caption-poc" / "out" / "base_1080.mp4",  # graded+upscaled base
    "transcript": ROOT / "videos" / "vex-sarvam" / "transcript.whisper.json",
    "source": ROOT / "videos" / "vex-sarvam" / "source.mp4",           # raw (for regenerate)
}

OUTPUTS = ROOT / "videos" / "reel-studio-out"
UPLOADS = ROOT / "videos" / "reel-studio-uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

PIPELINE_PY = ROOT / ".venv" / "bin" / "python"
RUNNER = Path(__file__).parent / "pipeline_runner.py"
PUBKIT = Path(__file__).parent / "publish_kit.py"
BROLL_PLAN = Path(__file__).parent / "broll_plan.py"
BROLL_FETCH = Path(__file__).parent / "broll_fetch.py"
ZOOM_PLAN = Path(__file__).parent / "zoom_plan.py"
CHAT_AGENT = Path(__file__).parent / "chat_agent.py"

# B-roll media cache — provider clips/images downloaded here (SSRF-guarded) and served
# Range-capable so Remotion's <Video>/<Img> render them reliably (see /api/broll/media).
BROLL_CACHE = ROOT / "videos" / "reel-studio-broll-cache"
SFX_ASSETS = ROOT / "videos" / "sfx_assets"  # promoted, read-only static catalog (see sfx_catalog.py)
MUSIC_ASSETS = ROOT / "videos" / "music_assets"  # promoted, read-only CC0 catalog (see music_catalog.py)
MUSIC_UPLOADS = ROOT / "videos" / "reel-studio-music-uploads"  # user's own uploaded tracks
BROLL_CACHE.mkdir(parents=True, exist_ok=True)
MUSIC_UPLOADS.mkdir(parents=True, exist_ok=True)
MAX_BROLL_BYTES = 256 * 1024 * 1024
MAX_MUSIC_BYTES = 50 * 1024 * 1024  # a music track is much smaller than a video upload

# Settings — API keys are READ-ONLY (present/missing only, never values). Defaults persist
# to a small JSON the editor reads on load.
ENV_PATH = ROOT / ".env"
KEY_NAMES = ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SARVAM_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"]
SETTINGS_PATH = Path(__file__).parent / "settings.json"
DEFAULT_SETTINGS = {
    "captionEngine": "pycaps",
    "captionStyle": "word-focus",
    "bottomPercent": 22,
    # Full-treatment defaults: clean audio + hook trim now ON alongside silence-trim + grade.
    "recipe": {"cleanAudio": True, "trimHook": True, "removeSilences": True, "removeRetakes": True, "colorGrade": True},
    "lufsDisplay": -14,
}
JOBS_TMP = Path(tempfile.gettempdir()) / "reel-studio-jobs"
JOBS_TMP.mkdir(parents=True, exist_ok=True)

# --- concurrency + resource limits (env-tunable; devops sizes these to the machine) ---
# NOTE: all job/project state below lives in THIS process's memory by design. Run a SINGLE
# uvicorn worker (--workers 1). Multiple workers/replicas would need a shared store (Redis).
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))  # heavy pipeline jobs at once
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
JOB_TTL_SEC = int(os.getenv("JOB_TTL_MIN", "15")) * 60           # finished jobs kept in memory
FILE_TTL_SEC = int(os.getenv("FILE_TTL_MIN", "120")) * 60        # scratch/uploads kept on disk
# BROLL_CACHE/MUSIC_UPLOADS have no per-project "in use" tracking (cache entries are addressed by
# a content hash or a random upload key, not a project id) — sweep by age alone, generously long
# so no realistic editing session ever loses a file mid-use, just to stop truly unbounded growth.
ASSET_TTL_SEC = int(os.getenv("ASSET_TTL_MIN", str(48 * 60))) * 60
# Admission control: the async task is created instantly (so SSE progress attaches), but the
# heavy subprocess only starts once a slot is free — a FIFO queue with a hard ceiling that also
# keeps us under the transcription provider's concurrency limit.
# NB: created lazily inside the running event loop — Python 3.9's asyncio.Semaphore binds to a
# loop at construction, so a module-level instance would attach to the wrong loop under uvicorn.
_JOB_SEM: "asyncio.Semaphore | None" = None


def _job_sem() -> asyncio.Semaphore:
    global _JOB_SEM
    if _JOB_SEM is None:
        _JOB_SEM = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    return _JOB_SEM

# ACTIVE = the legacy single-project fallback for callers that don't pass a project id (the
# desktop editor). Mobile mints a project id per upload and passes it on every call, so
# concurrent mobile users each read/write their OWN entry in `projects` and never clobber each
# other. Single-process store — run ONE uvicorn worker (see the note near the limits above).
ACTIVE: dict = dict(FIXTURE)
ACTIVE_VERSION = 1
projects: dict[str, dict] = {}  # project_id -> {name, video, transcript, source, version}


SEAM_MASK_SCALE = 1.16    # enough to read as a reframe, small enough not to crop into her hands
SEAM_MASK_ALT_SCALE = 1.07  # the other framing, for a seam that lands while SCALE is still held
SEAM_MASK_TOTAL_MS = 2700 # hold (1.2s) + ease-out; measured ~17% coverage at 7 seams on a 94s reel
SEAM_MASK_MIN_GAP_MS = 500  # below this a second change reads as a pulse, so leave it alone


def _seam_mask_zooms(existing: "list[dict]", project_id: "str | None" = None) -> "list[dict]":
    """A framing change placed exactly on each retake seam, to mask leftover body language.

    A word-bounded cut removes the words but not the gesture around them -- gesture stroke onset
    leads its phrase by 200-500ms and the preparation earlier still, so the speaker is already
    moving for a sentence that no longer exists and the seam jumps to an unrelated pose. Measured on
    a real seam, NO better cut point existed within +-300ms: the pause is too short to hide the
    gesture in, so the movement cannot be removed and must be masked. Changing framing at the cut is
    the standard fix; optical-flow morphing was rejected because Adobe's own guidance warns it turns
    blotchy on exactly this footage ("too much background, hand, or body movement").

    Skips any seam already covered by an editorial zoom -- that zoom is doing the masking already.
    Two scales alternate, because what masks a cut is the framing CHANGING across it, not the zoom
    being on: a seam arriving while the previous mask still holds 1.16 would sit in constant framing
    and get no masking at all, so it gets the other scale instead and the change still happens.
    """
    proj, _ = _resolve(project_id)
    d = proj.get("retakesDir")
    if not d:
        return []
    try:
        seams = json.loads((Path(d) / "retake_seams.json").read_text())
    except Exception:
        return []
    out: list[dict] = []
    last, last_scale = -10 ** 9, 1.0
    for s in sorted(int(x) for x in seams if isinstance(x, (int, float))):
        if any(z["startMs"] - 400 <= s <= z["endMs"] for z in existing):
            continue
        if s - last < SEAM_MASK_MIN_GAP_MS:
            continue
        held = last_scale if s < last + SEAM_MASK_TOTAL_MS else 1.0
        scale = SEAM_MASK_ALT_SCALE if held == SEAM_MASK_SCALE else SEAM_MASK_SCALE
        if out and out[-1]["endMs"] > s:
            out[-1]["endMs"] = s          # hand over cleanly instead of overlapping spans
        out.append({"startMs": s, "endMs": s + SEAM_MASK_TOTAL_MS,
                    "style": "seam_mask", "targetScale": scale})
        last, last_scale = s, scale
    return out


def _resolve(project_id: "str | None") -> "tuple[dict, int]":
    """Resolve a request's project. A known id -> its isolated entry (+ version); otherwise the
    legacy ACTIVE project (keeps id-less desktop callers working, and degrades gracefully)."""
    if project_id and project_id in projects:
        p = projects[project_id]
        return p, int(p.get("version", 1))
    return ACTIVE, ACTIVE_VERSION

app = FastAPI(title="Reel Editor backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ helpers --
def _num(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _probe(path: Path) -> dict:
    def q(*a) -> str:
        return subprocess.run(
            ["ffprobe", "-v", "error", *a, str(path)], capture_output=True, text=True
        ).stdout.strip()

    w = q("-select_streams", "v:0", "-show_entries", "stream=width", "-of", "default=nw=1:nk=1")
    h = q("-select_streams", "v:0", "-show_entries", "stream=height", "-of", "default=nw=1:nk=1")
    rate = q("-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1")
    dur = q("-show_entries", "format=duration", "-of", "default=nw=1:nk=1")
    fps = 60.0
    if "/" in rate:
        n, dn = rate.split("/", 1)
        fps = _num(n) / _num(dn, 1.0) if _num(dn, 1.0) else 60.0
    elif rate:
        fps = _num(rate, 60.0)
    return {
        "width": int(_num(w, 1080)),
        "height": int(_num(h, 1920)),
        "fps": round(fps) or 60,
        "durationMs": round(_num(dur) * 1000),
    }


def _build_words(whisper) -> list[dict]:
    segments = whisper.get("segments", []) if isinstance(whisper, dict) else whisper
    words: list[dict] = []
    idx = 0
    for seg in segments or []:
        for w in seg.get("words", []) or []:
            text = (w.get("word") or w.get("text") or "").strip()
            if not text:
                continue
            words.append({
                "id": f"w{idx}",
                "text": text,
                "startMs": round(_num(w.get("start")) * 1000),
                "endMs": round(_num(w.get("end")) * 1000),
            })
            idx += 1
    return words


# -------------------------------------------------------------- project API --
@app.get("/api/project")
def project(project: Optional[str] = None):
    proj, ver = _resolve(project)
    video = Path(proj["video"])
    whisper = json.loads(Path(proj["transcript"]).read_text())
    words = _build_words(whisper)
    meta = _probe(video)
    dur = meta["durationMs"] or (words[-1]["endMs"] + 2000 if words else 0)
    return {
        "name": proj["name"],
        "videoUrl": f"/api/video?v={ver}" + (f"&project={project}" if project else ""),
        "fps": meta["fps"],
        "width": meta["width"],
        "height": meta["height"],
        "durationMs": dur,
        "words": words,
    }


@app.get("/api/transcript")
def transcript(project: Optional[str] = None):
    proj, _ = _resolve(project)
    return JSONResponse(json.loads(Path(proj["transcript"]).read_text()))


@app.get("/api/video")
def video(project: Optional[str] = None, v: Optional[int] = None):
    proj, _ = _resolve(project)
    p = Path(proj["video"])
    if not p.exists():
        return JSONResponse({"error": f"video missing at {p}"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4")


@app.get("/api/health")
def health():
    return {"ok": True, "active": ACTIVE["name"], "video": Path(ACTIVE["video"]).exists(),
            "pipeline_py": PIPELINE_PY.exists()}


@app.get("/api/caption-styles")
def caption_styles_registry():
    """The caption-style registry (5 available + 2 coming-soon) + size presets + position bounds.
    The mobile frontend reads styles from here so it never re-declares style data."""
    return caption_styles.public_registry()


@app.get("/api/sfx-catalog")
def sfx_catalog_registry():
    """The SFX registry (17 sounds, 5 categories) for Fine-tune's sound-effects picker."""
    return sfx_catalog.public_registry()


def _safe_child(base: Path, key: str) -> Path | None:
    """Resolve `key` to a file strictly inside `base`, traversal-safe. `Path(x).name` alone does
    NOT neutralize a bare ".." (Path('..').name == '..'), and a LEXICAL `.parent == base` check
    then passes for it too (`(base / '..').parent == base` without touching the filesystem) — so
    both explicitly reject "." / ".." before the exists+parent check, which now only guards
    against unexpected shapes rather than being the sole line of defense."""
    name = Path(key or "").name
    if not name or name in (".", ".."):
        return None
    p = base / name
    return p if (p.exists() and p.parent == base) else None


@app.get("/api/sfx/media/{key}")
def sfx_media(key: str):
    p = _safe_child(SFX_ASSETS, key)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(p), media_type="audio/mpeg")


# SFX level matching + base boost. The bundled catalog was never level-matched, so one gainDb
# setting meant something different for every sound and the quieter half was inaudible under speech
# -- measured, a placed sound lifted the mix by ~1dB, which reads as "sound effects are not being
# added" when they are.
#
# Normalised on PEAK, not RMS. RMS was tried first and measured WORSE (catalogue audibility spread
# 10.0dB -> 11.4dB): whole-file RMS is not comparable when durations run 0.10s to 4.14s, because a
# long decaying hit and a short pop put their energy in completely different places. Peak equalises
# the attack, which is what actually registers for a transient accent. Measured effect of this
# version: sounds reaching a clearly-audible lift went from 8/17 to 13/17.
SFX_TARGET_PEAK_DB = -1.0   # common ceiling to normalise the attack to
SFX_BASE_BOOST_DB = 6.0     # an accent belongs momentarily ABOVE the dialogue, not level with it
SFX_MAKEUP_CLAMP = 10.0     # never trust a wild measurement into a huge boost
_sfx_makeup_cache: dict[str, float] = {}


def _sfx_makeup_db(path: Path) -> float:
    """dB to add so this sound's attack lands at SFX_TARGET_PEAK_DB, plus the base boost.
    Measured once per file per process (ffmpeg volumedetect)."""
    key = str(path)
    if key in _sfx_makeup_cache:
        return _sfx_makeup_cache[key]
    makeup = SFX_BASE_BOOST_DB
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
                              "-f", "null", "-"], capture_output=True, text=True, timeout=60).stderr
        for line in out.splitlines():
            if "max_volume:" in line:
                peak = float(line.split("max_volume:")[1].replace("dB", "").strip())
                makeup = min(SFX_MAKEUP_CLAMP, max(0.0, SFX_TARGET_PEAK_DB - peak)) + SFX_BASE_BOOST_DB
                break
    except Exception:
        pass   # unmeasurable -> base boost only, rather than failing the render
    _sfx_makeup_cache[key] = makeup
    return makeup


def _sfx_id_to_path(sfx_id: str) -> Path | None:
    sound = sfx_catalog.SFX_BY_ID.get(str(sfx_id or ""))
    if not sound:
        return None
    p = SFX_ASSETS / sound["file"]
    return p if p.exists() else None


@app.get("/api/music-catalog")
def music_catalog_registry():
    """The bundled CC0 music registry (40 tracks, 8 categories) + ducking presets for
    Fine-tune's Music sheet. User-uploaded tracks are NOT listed here (they only exist inside
    a single project's own state) — see POST /api/music/upload."""
    return music_catalog.public_registry()


@app.get("/api/music/media/{key}")
def music_media(key: str):
    for d in (MUSIC_ASSETS, MUSIC_UPLOADS):
        p = _safe_child(d, key)
        if p is not None:
            return FileResponse(str(p), media_type="audio/mpeg")
    return JSONResponse({"error": "not found"}, status_code=404)


def _music_path(id_or_key: str) -> Path | None:
    """Resolve either a catalog track id (e.g. "montage") or an uploaded key (e.g. "up_xyz.mp3")
    to its real file. Catalog first — ids and upload keys never collide (uploads are always
    "up_"-prefixed), but checking catalog first means a track id always wins if it somehow did."""
    track = music_catalog.MUSIC_BY_ID.get(str(id_or_key or ""))
    if track:
        p = MUSIC_ASSETS / track["file"]
        return p if p.exists() else None
    return _safe_child(MUSIC_UPLOADS, str(id_or_key or ""))


@app.post("/api/music/upload")
async def music_upload(request: Request, name: str = "track.mp3"):
    """User's own music track (Setup's Music sheet Upload tab) — consent to use is implied by
    the act of uploading, same as this app's existing user-uploaded video/B-roll paths; no
    rights-checking is performed (matches every competitor's user-upload model)."""
    safe = Path(name).name or "track.mp3"
    ext = Path(safe).suffix.lower() or ".mp3"
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        return JSONResponse({"error": "unsupported audio format"}, status_code=400)
    key = f"up_{uuid.uuid4().hex[:12]}{ext}"
    dest = MUSIC_UPLOADS / key
    total = 0
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_MUSIC_BYTES:
                    dest.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": f"file too large (max {MAX_MUSIC_BYTES // (1024 * 1024)} MB)"},
                        status_code=413,
                    )
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "empty upload"}, status_code=400)
    dur_ms = (await asyncio.get_event_loop().run_in_executor(None, _probe, dest)).get("durationMs", 0)
    return {"key": key, "url": f"/api/music/media/{key}", "durationMs": dur_ms, "size": dest.stat().st_size}


# ------------------------------------------------------------------- jobs ----
class Job:
    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.done: bool = False
        self.meta: dict = {}
        self.finished_at: float | None = None  # wall-clock when finished (for TTL cleanup)
        self.cond = asyncio.Condition()

    async def push(self, obj: dict) -> None:
        async with self.cond:
            self.lines.append(obj)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.finished_at = time.time()
            self.lines.append({"event": "_end"})
            self.cond.notify_all()


jobs: dict[str, Job] = {}


def _in_use_paths() -> set[str]:
    """Every path a live project still references — the janitor must never delete these.
    Includes "pregrade" (the Look picker's re-gradeable checkpoint, see _activate_ingest) — it
    lives in UPLOADS just like video/transcript/source and is just as much "still referenced"
    for as long as the project exists, even if the user never opens the Look sheet."""
    keys = ("video", "transcript", "source", "pregrade")
    keep = {str(ACTIVE.get(k)) for k in keys if ACTIVE.get(k)}
    for p in projects.values():
        keep.update(str(p[k]) for k in keys if p.get(k))
    return keep


async def _janitor() -> None:
    """Periodic TTL sweep so memory + disk don't grow forever (single-process cleanup)."""
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            # 1) drop finished jobs (frees the in-memory NDJSON buffers) + their scratch files
            stale = [jid for jid, j in list(jobs.items())
                     if j.done and j.finished_at and now - j.finished_at > JOB_TTL_SEC]
            for jid in stale:
                jobs.pop(jid, None)
                for suf in (".payload.json", ".transcript.json"):
                    (JOBS_TMP / f"{jid}{suf}").unlink(missing_ok=True)
            # 2) sweep any orphaned scratch files by age
            for p in JOBS_TMP.glob("*"):
                try:
                    if now - p.stat().st_mtime > JOB_TTL_SEC:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass
            # 3) age out uploaded sources/transcripts NOT still referenced by a live project
            keep = _in_use_paths()
            for p in UPLOADS.glob("*"):
                try:
                    if str(p) not in keep and now - p.stat().st_mtime > FILE_TTL_SEC:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass
            # 4) age out stock B-roll cache + user-uploaded music — no per-project reference
            # tracking exists for either (see ASSET_TTL_SEC's comment), so this is age-only,
            # generously long, just to cap otherwise-permanent multi-user disk growth.
            for d in (BROLL_CACHE, MUSIC_UPLOADS):
                for p in d.glob("*"):
                    try:
                        if now - p.stat().st_mtime > ASSET_TTL_SEC:
                            p.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception:
            pass  # a janitor hiccup must never kill the loop


@app.on_event("startup")
async def _start_janitor() -> None:
    asyncio.create_task(_janitor())


@app.on_event("startup")
async def _start_db() -> None:
    # The only thing in this backend that persists across a restart — see db.py's docstring
    # for why this is deliberately narrow (asset-library rows + small preference blobs only,
    # NOT job/project state, which stays in-memory by design).
    db.init_db()


def _activate_ingest(meta: dict, obj: dict) -> None:
    global ACTIVE, ACTIVE_VERSION
    proj = {
        "name": meta.get("name", "uploaded clip"),
        "video": obj["output"],
        "transcript": obj["transcript"],
        "source": meta.get("source", obj["output"]),
        # Cached pre-grade checkpoint (may be None if the copy failed) — lets the Fine-tune
        # "Look" picker re-grade later without a full re-ingest. See pipeline_runner.py's
        # run_ingest / reel-studio/pipeline.py's regrade().
        "pregrade": obj.get("pregrade"),
        # This project's vex working dir -- pre_retakes.mp4 + retake_cuts.json (if any retakes
        # were auto-cut) live here. Read by GET /api/retakes and POST /api/retakes/restore.
        "retakesDir": obj.get("retakesDir"),
        "retakesRestored": [],  # ids currently excluded from the auto-cut (Trimmed sheet state)
    }
    pid = meta.get("project_id")
    if pid:  # this user's isolated entry, with its own version counter
        prev = int(projects.get(pid, {}).get("version", 0))
        projects[pid] = {**proj, "version": prev + 1}
    ACTIVE = {**proj}  # legacy fallback for id-less callers (desktop)
    ACTIVE_VERSION += 1


def _activate_restore_retakes(meta: dict, obj: dict) -> None:
    """On a successful restore job: swap the resolved project's `video` to the re-applied
    output, and remember which ids are now restored so GET /api/retakes and the Trimmed sheet's
    checkboxes reflect the current state on the next fetch. `keep_ids` was echoed back in job
    meta at spawn time (not in the pipeline's own "done" event, which only knows about files)."""
    global ACTIVE, ACTIVE_VERSION
    new_video = obj.get("output")
    if not new_video:
        return
    # Repoint the transcript TOO, not just the video. The caption render reads proj["transcript"],
    # so leaving it on the ingest-time file meant every applied trim shortened the video while the
    # captions kept their old timings -- misaligned from the first new cut onward and worsening down
    # the timeline. Only swap when the rebuild actually produced a file; a missing one means keep the
    # old pairing rather than point at nothing.
    new_transcript = obj.get("transcript")
    ok_transcript = bool(new_transcript) and Path(str(new_transcript)).exists()
    pid = meta.get("project_id")
    if pid and pid in projects:
        prev = int(projects[pid].get("version", 1))
        projects[pid] = {
            **projects[pid], "video": new_video, "version": prev + 1,
            "retakesRestored": meta.get("keep_ids") or [],
            **({"transcript": str(new_transcript)} if ok_transcript else {}),
        }
    if not pid:
        ACTIVE = {**ACTIVE, "video": new_video,
                  **({"transcript": str(new_transcript)} if ok_transcript else {})}
        ACTIVE_VERSION += 1


def _activate_regrade(meta: dict, obj: dict) -> None:
    """On a successful regrade job: swap the resolved project's `video` to the newly-graded
    output (transcript/source/pregrade all untouched — only the base's color changed), bump
    version so the mobile client's cache-busting query param picks up the new file. `pregrade`
    is deliberately NOT touched so the user can keep trying different looks from the SAME
    checkpoint any number of times."""
    global ACTIVE, ACTIVE_VERSION
    new_video = obj.get("output")
    if not new_video:
        return
    pid = meta.get("project_id")
    if pid and pid in projects:
        prev = int(projects[pid].get("version", 1))
        projects[pid] = {**projects[pid], "video": new_video, "version": prev + 1}
    if not pid:
        ACTIVE = {**ACTIVE, "video": new_video}
        ACTIVE_VERSION += 1


async def _run_job(job_id: str, mode: str, payload_path: Path) -> None:
    job = jobs[job_id]
    sem = _job_sem()  # created on first use, bound to the running loop
    if sem.locked():  # every slot busy -> let the user know they're queued, not stuck
        await job.push({"event": "log", "text": "Queued — waiting for a free slot…"})
    # Admission control: block here until a slot frees, so at most MAX_CONCURRENT_JOBS heavy
    # pipeline subprocesses run at once (protects CPU/RAM + the shared transcription API key).
    async with sem:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # stream child prints live
        proc = await asyncio.create_subprocess_exec(
            str(PIPELINE_PY), str(RUNNER), mode, str(payload_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )

        async def pump(stream: asyncio.StreamReader, is_err: bool) -> None:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", "replace").rstrip("\n")
                if not text.strip():
                    continue
                try:
                    obj = json.loads(text)
                    if not isinstance(obj, dict) or "event" not in obj:
                        raise ValueError
                except Exception:
                    obj = {"event": "log", "text": text}
                    if is_err:
                        obj["stream"] = "stderr"
                if obj.get("event") == "done" and mode == "ingest":
                    _activate_ingest(job.meta, obj)
                elif obj.get("event") == "done" and mode == "regrade":
                    _activate_regrade(job.meta, obj)
                elif obj.get("event") == "done" and mode == "restore_retakes":
                    _activate_restore_retakes(job.meta, obj)
                await job.push(obj)

        await asyncio.gather(pump(proc.stdout, False), pump(proc.stderr, True))
        rc = await proc.wait()
    if not any(l.get("event") in ("done", "error") for l in job.lines):
        await job.push({"event": "error", "message": f"runner exited {rc} without a result"})
    await job.finish()


def _spawn(mode: str, payload: dict, meta: dict | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = Job()
    if meta:
        jobs[job_id].meta = meta
    payload_path = JOBS_TMP / f"{job_id}.payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, mode, payload_path))
    return job_id


def _reconstruct_transcript(edited_text: dict, project_id: "str | None" = None) -> dict:
    """Copy the resolved project's whisper_json, apply per-word text edits (id scheme w{index},
    same as _build_words). Does NOT apply cutWordIds or highlight overrides."""
    proj, _ = _resolve(project_id)
    wj = json.loads(Path(proj["transcript"]).read_text())
    idx = 0
    for seg in wj.get("segments", []):
        for w in seg.get("words", []):
            text = str(w.get("word") or w.get("text") or "").strip()
            if not text:
                continue
            new = edited_text.get(f"w{idx}")
            if new and str(new).strip():
                w["word"] = str(new).strip()
            idx += 1
    return wj


#  vex/color_grading.py's SUPPORTED_COLOR_GRADE_LOOKS, duplicated here (not imported — this
# backend never imports vex code directly; it always shells out to the vex venv subprocess,
# see _run_vex_json below) purely so a bad `color_grade_look` value fails fast with a clear
# 400 here instead of surfacing as an opaque subprocess failure deep in the ingest job.
COLOR_GRADE_LOOKS = ("auto", "natural", "vibrant", "cinematic", "warm", "cool", "documentary", "punchy")


@app.post("/api/upload")
async def upload(
    request: Request,
    name: str = "clip.mp4",
    clean_audio: bool = True,
    remove_silences: bool = True,
    remove_retakes: bool = True,
    color_grade: bool = True,
    color_grade_look: str = "natural",
    color_grade_intensity: float = 0.5,
):
    if color_grade_look not in COLOR_GRADE_LOOKS:
        return JSONResponse(
            {"error": f"color_grade_look must be one of {COLOR_GRADE_LOOKS}"}, status_code=400
        )
    if not (0.0 <= color_grade_intensity <= 1.5):
        return JSONResponse({"error": "color_grade_intensity must be between 0.0 and 1.5"}, status_code=400)
    safe = Path(name).name or "clip.mp4"
    dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{safe}"
    total = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:  # abort + drop the partial so one upload can't fill the disk
                f.close()
                dest.unlink(missing_ok=True)
                return JSONResponse(
                    {"error": f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"},
                    status_code=413,
                )
            f.write(chunk)
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "empty upload"}, status_code=400)
    job_id = uuid.uuid4().hex[:12]
    out_transcript = UPLOADS / f"{job_id}.whisper.json"
    payload = {
        "source_video": str(dest),
        "output_transcript": str(out_transcript),
        # Recipe comes from the caller (mobile toggles); defaults = cleaned, silence-trimmed,
        # graded & upscaled. (Trim-to-hook removed from the mobile flow.)
        "recipe": {
            "cleanAudio": bool(clean_audio),
            "removeSilences": bool(remove_silences), "removeRetakes": bool(remove_retakes),
            "colorGrade": bool(color_grade),
            "colorGradeLook": color_grade_look, "colorGradeIntensity": float(color_grade_intensity),
        },
    }
    jobs[job_id] = Job()
    # reuse job_id as the project_id: minted here, returned to the client, threaded back on
    # every later call so this upload's project stays isolated from other users'.
    jobs[job_id].meta = {"name": safe, "source": str(dest), "project_id": job_id}
    pp = JOBS_TMP / f"{job_id}.payload.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, "ingest", pp))
    # probe the uploaded source off the event loop (clip length for the Setup screen)
    try:
        src_dur = (await asyncio.get_event_loop().run_in_executor(None, _probe, dest)).get("durationMs", 0)
    except Exception:
        src_dur = 0
    return {"job_id": job_id, "project_id": job_id, "name": safe,
            "createdAt": int(time.time() * 1000), "sourceSizeBytes": total, "sourceDurationMs": src_dur}


@app.post("/api/color-grade/preview")
async def color_grade_preview(body: dict):
    """Render one small preview thumbnail per requested look, on THIS project's OWN base video
    (not a generic swatch) — so picking a look is informed by real footage, in both Setup and
    Fine-tune. Cheap/synchronous (a handful of single-frame ffmpeg renders via the fast
    non-shot-aware path) — unlike an actual regrade, which is a real job with progress."""
    pid = body.get("project")
    proj, _ = _resolve(pid)
    requested = body.get("looks") or list(COLOR_GRADE_LOOKS)
    looks = [l for l in requested if l in COLOR_GRADE_LOOKS]
    if not looks:
        return JSONResponse({"error": f"looks must be a subset of {COLOR_GRADE_LOOKS}"}, status_code=400)
    intensity = float(body.get("intensity", 0.5))
    if not (0.0 <= intensity <= 1.5):
        return JSONResponse({"error": "intensity must be between 0.0 and 1.5"}, status_code=400)
    meta = await asyncio.get_event_loop().run_in_executor(None, _probe, Path(proj["video"]))
    dur_s = (meta.get("durationMs") or 4000) / 1000.0
    timestamp_s = min(2.0, max(0.2, dur_s * 0.2))  # a bit into the clip; never past a short clip's end
    result = await _run_vex_json(
        Path(__file__).parent / "color_grade_preview.py",
        {"video_path": str(proj["video"]), "timestamp_s": timestamp_s, "looks": looks, "intensity": intensity},
        timeout=60.0,
    )
    if result.get("error"):
        return JSONResponse(result, status_code=502)
    return result


@app.post("/api/color-grade/apply")
async def color_grade_apply(body: dict):
    """Fine-tune's "Look" picker Apply action: re-grade the project's cached pre-grade
    checkpoint with a new look/intensity WITHOUT re-transcribing (see pipeline_runner.py's
    run_regrade / reel-studio/pipeline.py's regrade()), then swap it in as the project's base.
    Returns a job_id — the client streams progress the SAME way as every other job
    (GET /api/progress/{job_id}), then calls the existing render endpoint to re-bake overlays
    on top of the newly-graded base."""
    pid = body.get("project")
    proj, _ = _resolve(pid)
    pregrade = proj.get("pregrade")
    if not pregrade:
        return JSONResponse(
            {"error": "no cached pre-grade checkpoint for this project (try re-uploading)"}, status_code=400
        )
    look = str(body.get("look", "natural"))
    if look not in COLOR_GRADE_LOOKS:
        return JSONResponse({"error": f"look must be one of {COLOR_GRADE_LOOKS}"}, status_code=400)
    intensity = float(body.get("intensity", 0.5))
    if not (0.0 <= intensity <= 1.5):
        return JSONResponse({"error": "intensity must be between 0.0 and 1.5"}, status_code=400)
    job_id = uuid.uuid4().hex[:12]
    out_path = UPLOADS / f"{job_id}.regraded.mp4"
    payload = {"pregrade_video": pregrade, "output_path": str(out_path), "look": look, "intensity": intensity}
    jobs[job_id] = Job()
    if pid:
        jobs[job_id].meta = {"project_id": pid}
    pp = JOBS_TMP / f"{job_id}.payload.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, "regrade", pp))
    return {"job_id": job_id}


# Local copies of reel-studio/pipeline.py's _merge_cut_ranges / remap_to_output. Deliberately
# duplicated rather than imported: pipeline.py only imports cleanly inside the VEX venv (it does
# sys.path/os.chdir setup and pulls in vex's engine), while this server runs on system Python 3.9
# -- importing it here would drag that whole runtime in just for ~10 lines of interval math. Kept
# byte-for-byte equivalent in behavior; the authoritative versions live in pipeline.py, which is
# what actually performs the cuts. Any change there must be mirrored here.
def _merge_cut_ranges_py(ranges) -> list:
    out: list = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if out and a <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([max(a, 0.0), max(b, 0.0)])
    return out


def _remap_to_output_py(t: float, merged: list) -> float:
    removed_before = sum(min(b, t) - a for a, b in merged if a < t)
    return max(0.0, t - removed_before)


def _remap_to_source_py(t_out: float, merged: list) -> float:
    t_src = t_out
    for a, b in merged:
        if a <= t_src:
            t_src += (b - a)
        else:
            break
    return max(0.0, t_src)


@app.get("/api/retakes")
async def get_retakes(project: Optional[str] = None):
    """Fine-tune "Trimmed" sheet's data source: every retake cut (AI-detected or user-marked --
    text, reason, confidence), which are currently restored, the FULL pre-retake-cut transcript
    (so the client can render a tap-to-mark timeline against real segment boundaries), and how
    much the video shrank from silence-trim vs. retake-cut -- powers the ready-screen summary
    line too. Returns an empty/zeroed shape (not an error) when nothing was auto-cut, same "just
    isn't offered" fallback as color-grade's pregrade checkpoint."""
    proj, _ = _resolve(project)
    retakes_dir = proj.get("retakesDir")
    empty = {"retakes": [], "segments": [], "preRetakeDurationMs": None,
             "silenceTrimmedMs": 0, "retakesTrimmedMs": 0,
             "originalDurationMs": None, "currentDurationMs": None}
    if not retakes_dir:
        return empty
    wd = Path(retakes_dir)
    ledger_path = wd / "retake_cuts.json"
    pre_video = wd / "pre_retakes.mp4"
    pre_segs_path = wd / "pre_retakes.segments.json"
    # The checkpoint is saved unconditionally (whenever silence-trim ran), independent of
    # whether retake-removal itself was on -- so a MISSING ledger (retakes toggle was off, or
    # nothing was cut) still needs the duration numbers below, not the fully-empty shape. Only
    # bail out early if the checkpoint itself was never created (silence-trim never ran either).
    if not pre_video.exists():
        return empty
    try:
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    except Exception:
        ledger = []
    try:
        pre_segs = json.loads(pre_segs_path.read_text()) if pre_segs_path.exists() else []
    except Exception:
        pre_segs = []
    restored = set(proj.get("retakesRestored") or [])

    loop = asyncio.get_event_loop()
    orig_meta = await loop.run_in_executor(None, _probe, Path(proj["source"])) if proj.get("source") else {}
    pre_meta = await loop.run_in_executor(None, _probe, pre_video)
    cur_meta = await loop.run_in_executor(None, _probe, Path(proj["video"])) if proj.get("video") else {}
    orig_ms, pre_ms, cur_ms = orig_meta.get("durationMs"), pre_meta.get("durationMs"), cur_meta.get("durationMs")

    # Cut ranges currently ACTIVE (not restored) define the mapping between the checkpoint's
    # timeline and the rendered output's timeline. `outStartMs` is where each cut sits on the
    # FINAL video's own clock -- a zero-length seam, since its content is gone there -- which is
    # what lets the Trimmed sheet mark up the real rendered reel, not just the bare checkpoint.
    active_ranges = sorted(
        (float(e["cutStartMs"]) / 1000.0, float(e["cutEndMs"]) / 1000.0)
        for e in ledger
        if isinstance(e, dict) and str(e.get("id")) not in restored
        and e.get("cutStartMs") is not None and e.get("cutEndMs") is not None
    )
    merged = _merge_cut_ranges_py(active_ranges)

    return {
        "retakes": [
            {
                "id": str(e.get("id")), "text": str(e.get("text", "")),
                "reason": str(e.get("reason", "")), "confidence": e.get("confidence", "medium"),
                "startMs": e.get("startMs"), "endMs": e.get("endMs"),
                "cutStartMs": e.get("cutStartMs"), "cutEndMs": e.get("cutEndMs"),
                "restored": str(e.get("id")) in restored,
                # Position on the RENDERED output's timeline (None when this cut is restored --
                # a restored cut's content is present, so it has no seam to point at).
                "outAtMs": (
                    None if str(e.get("id")) in restored or e.get("cutStartMs") is None
                    else int(_remap_to_output_py(float(e["cutStartMs"]) / 1000.0, merged) * 1000)
                ),
            }
            for e in ledger if isinstance(e, dict)
        ],
        # Segments carry their OWN words, so the client can render a line as a row (tap = select
        # the whole line) and expand that row into per-word chips when a sub-line trim is needed.
        # That's the precision mechanism: a word chip in a list row is ~9-10mm tall, which clears
        # the one-handed-thumb threshold, whereas one second on the fit-zoom timeline is ~1.35pt
        # (~0.2mm) -- two orders of magnitude below the ~5mm floor where a touch target stops
        # trading size for error. Aiming happens in the transcript; the bar is for orientation.
        "segments": [
            {
                "segIndex": i, "text": str(s.get("text", "")),
                "startMs": int(float(s.get("start", 0)) * 1000), "endMs": int(float(s.get("end", 0)) * 1000),
                "words": [
                    {
                        "startMs": int(float(w.get("start", 0)) * 1000),
                        "endMs": int(float(w.get("end", 0)) * 1000),
                        "text": str(w.get("text", "")),
                    }
                    for w in (s.get("words") or []) if isinstance(w, dict)
                ],
            }
            for i, s in enumerate(pre_segs) if isinstance(s, dict)
        ],
        # Flat word list too -- still used for the timeline's own word-tick landmarks.
        "words": [
            {
                "startMs": int(float(w.get("start", 0)) * 1000),
                "endMs": int(float(w.get("end", 0)) * 1000),
                "text": str(w.get("text", "")),
            }
            for s in pre_segs if isinstance(s, dict)
            for w in (s.get("words") or []) if isinstance(w, dict)
        ],
        "preRetakeDurationMs": pre_ms,
        "silenceTrimmedMs": max(0, orig_ms - pre_ms) if (orig_ms and pre_ms) else 0,
        "retakesTrimmedMs": max(0, pre_ms - cur_ms) if (pre_ms and cur_ms) else 0,
        "originalDurationMs": orig_ms,
        "currentDurationMs": cur_ms,
    }


@app.get("/api/retakes/preview")
def retakes_preview(project: Optional[str] = None):
    """Streams the pre_retakes.mp4 checkpoint (post-silence-trim, pre-retake-cut) for the
    Trimmed sheet's preview player -- Range-served the same way /api/result serves finished
    reels, so scrubbing/seeking works. `project` comes from _resolve (server-tracked path, never
    user-supplied), so there's no path-traversal surface here despite reading outside UPLOADS."""
    proj, _ = _resolve(project)
    retakes_dir = proj.get("retakesDir")
    if not retakes_dir:
        return JSONResponse({"error": "no retake checkpoint for this project"}, status_code=404)
    p = Path(retakes_dir) / "pre_retakes.mp4"
    if not p.exists():
        return JSONResponse({"error": "no retake checkpoint for this project"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4")


@app.get("/api/retakes/final")
def retakes_final(project: Optional[str] = None):
    """Streams the project's CURRENT base -- i.e. the already-cut video the finished reel is
    built from -- so the Trimmed sheet can review cuts against the real result instead of only
    the bare pre-cut checkpoint. Same Range-served FileResponse pattern as /api/retakes/preview.
    (The fully-composited reel with captions/B-roll/music is served by /api/result/{name}, which
    the client already has; this endpoint is the base that cut positions map onto.)"""
    proj, _ = _resolve(project)
    v = proj.get("video")
    if not v or not Path(v).exists():
        return JSONResponse({"error": "no current base video for this project"}, status_code=404)
    return FileResponse(str(v), media_type="video/mp4")


@app.post("/api/retakes/mark")
async def retakes_mark(body: dict):
    """Trimmed sheet's manual trim action: append a user-marked cut to the ledger.

    Accepts an explicit range, in EITHER timeline:
      - `startMs`/`endMs`            -> checkpoint (pre-cut) timeline; used by the bare preview
      - `outStartMs`/`outEndMs`      -> the CURRENT rendered base's timeline; used when marking
                                        while watching the real result. Converted back to the
                                        checkpoint timeline via the inverse of the cut mapping,
                                        since the ledger is only ever expressed in checkpoint time.
    Both edges snap OUTWARD to the nearest word boundary, so a cut never lands mid-word and the
    user never needs pinch-zoom precision. Does NOT re-render by itself -- the client calls
    /api/retakes/restore afterward, same as toggling any other row."""
    pid = body.get("project")
    proj, _ = _resolve(pid)
    retakes_dir = proj.get("retakesDir")
    if not retakes_dir:
        return JSONResponse({"error": "no retake checkpoint for this project"}, status_code=400)
    wd = Path(retakes_dir)
    pre_segs_path = wd / "pre_retakes.segments.json"
    if not pre_segs_path.exists():
        return JSONResponse({"error": "no retake checkpoint for this project"}, status_code=400)

    ledger_path = wd / "retake_cuts.json"
    try:
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    except Exception:
        ledger = []
    restored = set(proj.get("retakesRestored") or [])

    in_out_timeline = body.get("outStartMs") is not None and body.get("outEndMs") is not None
    try:
        if in_out_timeline:
            raw_a, raw_b = float(body["outStartMs"]) / 1000.0, float(body["outEndMs"]) / 1000.0
            active = sorted(
                (float(e["cutStartMs"]) / 1000.0, float(e["cutEndMs"]) / 1000.0)
                for e in ledger
                if isinstance(e, dict) and str(e.get("id")) not in restored
                and e.get("cutStartMs") is not None and e.get("cutEndMs") is not None
            )
            merged = _merge_cut_ranges_py(active)
            start_s = _remap_to_source_py(raw_a, merged)
            end_s = _remap_to_source_py(raw_b, merged)
        else:
            start_s, end_s = float(body["startMs"]) / 1000.0, float(body["endMs"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "startMs/endMs (or outStartMs/outEndMs) required"}, status_code=400)
    if end_s <= start_s:
        return JSONResponse({"error": "end must be after start"}, status_code=400)

    pre_segs = json.loads(pre_segs_path.read_text())
    words = [w for s in pre_segs if isinstance(s, dict)
             for w in (s.get("words") or []) if isinstance(w, dict)]
    # Snap OUTWARD: start back to the start of the word it lands in/before, end forward to the
    # end of the word it lands in/after -- so a partially-selected word is fully removed rather
    # than clipped mid-syllable.
    starts = [float(w["start"]) for w in words]
    ends = [float(w["end"]) for w in words]
    if words:
        inside_start = [s for s in starts if s <= start_s]
        snap_a = max(inside_start) if inside_start else min(starts)
        inside_end = [e for e in ends if e >= end_s]
        snap_b = min(inside_end) if inside_end else max(ends)
        # Only trust the snap when it stays near the request; a tap in a long silence with no
        # nearby word would otherwise get dragged onto a distant word.
        snapped_a = abs(snap_a - start_s) <= 1.5
        snapped_b = abs(snap_b - end_s) <= 1.5
        start_s = snap_a if snapped_a else start_s
        end_s = snap_b if snapped_b else end_s
        # Report when an edge could NOT be word-aligned instead of quietly cutting at the raw
        # requested time. On a fit-zoom timeline a finger's own positional noise is several
        # seconds wide -- far larger than this 1.5s guard -- so most bar taps land here, and the
        # old silent fallback produced a cut nowhere near any word with no signal to the user.
        # `snapped` is echoed back so the client can say so rather than pretend it was precise.
        fully_snapped = bool(snapped_a and snapped_b)
    else:
        fully_snapped = False
    start_s = max(0.0, start_s)
    end_s = max(start_s + 0.05, end_s)

    covered = " ".join(
        str(w.get("text", "")).strip() for w in words
        if float(w["start"]) >= start_s - 0.01 and float(w["end"]) <= end_s + 0.01
    ).strip()

    existing = {str(e.get("id")) for e in ledger if isinstance(e, dict)}
    n = 0
    while f"u{n}" in existing:
        n += 1
    entry_id = f"u{n}"
    ledger.append({
        "id": entry_id,
        "text": covered or "(no speech in this range)",
        "cutStartMs": int(start_s * 1000), "cutEndMs": int(end_s * 1000),
        "startMs": int(start_s * 1000), "endMs": int(end_s * 1000),
        "reason": "Trimmed by you", "confidence": "manual",
    })
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
    return {"id": entry_id, "startMs": int(start_s * 1000), "endMs": int(end_s * 1000),
            "text": covered, "snapped": fully_snapped}


@app.post("/api/retakes/unmark")
async def retakes_unmark(body: dict):
    """Remove a USER-marked cut from the ledger entirely (tapping a red region off). Only
    manual entries are deletable -- an AI-detected cut is 'restored' via keepIds instead, so its
    reasoning/confidence stays visible in the sheet rather than silently disappearing."""
    pid = body.get("project")
    proj, _ = _resolve(pid)
    retakes_dir = proj.get("retakesDir")
    if not retakes_dir:
        return JSONResponse({"error": "no retake checkpoint for this project"}, status_code=400)
    ledger_path = Path(retakes_dir) / "retake_cuts.json"
    if not ledger_path.exists():
        return JSONResponse({"error": "nothing marked yet"}, status_code=400)
    target = str(body.get("id") or "")
    try:
        ledger = json.loads(ledger_path.read_text())
    except Exception:
        ledger = []
    kept = [e for e in ledger
            if not (isinstance(e, dict) and str(e.get("id")) == target and e.get("confidence") == "manual")]
    if len(kept) == len(ledger):
        return JSONResponse({"error": "no manual cut with that id"}, status_code=400)
    ledger_path.write_text(json.dumps(kept, ensure_ascii=False, indent=1))
    return {"removed": target}


@app.post("/api/retakes/restore")
async def retakes_restore(body: dict):
    """Fine-tune "Trimmed" sheet's restore action: re-apply the retake-cut ledger against its
    checkpoint with `keepIds` excluded from the cut (i.e. restored), then swap in as the
    project's base -- same job/SSE pattern as color-grade apply, then the client re-renders on
    top of the new base via the existing base_override flow. `keepIds` is the FULL current set
    of restored ids the client wants, not a delta, matching every other Fine-tune toggle."""
    pid = body.get("project")
    proj, _ = _resolve(pid)
    retakes_dir = proj.get("retakesDir")
    if not retakes_dir:
        return JSONResponse(
            {"error": "no retake checkpoint for this project (nothing was auto-cut)"}, status_code=400
        )
    keep_ids_in = body.get("keepIds")
    keep_ids = [str(x) for x in keep_ids_in] if isinstance(keep_ids_in, list) else []
    job_id = uuid.uuid4().hex[:12]
    out_path = UPLOADS / f"{job_id}.retakes-restored.mp4"
    # A re-cut changes the timeline, so the caption transcript has to be rebuilt with it. Written to
    # a NEW file rather than over the ingest one: if the restore fails half way, the project keeps a
    # transcript that still matches its current video instead of being left with neither.
    out_transcript = UPLOADS / f"{job_id}.retakes-restored.whisper.json"
    payload = {"working_dir": retakes_dir, "output_path": str(out_path), "keep_ids": keep_ids,
               "output_transcript": str(out_transcript)}
    jobs[job_id] = Job()
    if pid:
        jobs[job_id].meta = {"project_id": pid, "keep_ids": keep_ids}
    pp = JOBS_TMP / f"{job_id}.payload.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, "restore_retakes", pp))
    return {"job_id": job_id}


@app.post("/api/recaption")
async def recaption(body: dict):
    settings = body.get("captionSettings", {})
    edits = body.get("edits", {})
    pid = body.get("project")
    proj, _ = _resolve(pid)
    wj = _reconstruct_transcript(edits.get("editedText", {}), pid)
    job_id = uuid.uuid4().hex[:12]
    tj = JOBS_TMP / f"{job_id}.transcript.json"
    tj.write_text(json.dumps(wj, ensure_ascii=False))
    out_name = f"recap_{job_id}.mp4"
    payload = {
        "caption_base_video": str(proj["video"]),
        "transcript_json_path": str(tj),
        "output_path": str(OUTPUTS / out_name),
        "caption_engine": settings.get("engine", "remotion"),
        "bottom_percent": settings.get("bottomPercent", 22),
        "style": settings.get("style", "word-focus"),
        "fps": 60,
    }
    jobs[job_id] = Job()
    pp = JOBS_TMP / f"{job_id}.payload.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, "recaption", pp))
    return {"job_id": job_id, "engine": payload["caption_engine"], "output_name": out_name}


@app.post("/api/regenerate")
async def regenerate(body: dict):
    settings = body.get("captionSettings", {})
    recipe = body.get("recipe", {})
    proj, _ = _resolve(body.get("project"))
    payload = {
        "source_video": str(proj["source"]),
        "caption_engine": settings.get("engine", "remotion"),
        "recipe": recipe,
    }
    job_id = _spawn("regenerate", payload)
    return {"job_id": job_id, "engine": payload["caption_engine"]}


@app.get("/api/progress/{job_id}")
async def progress(job_id: str):
    job = jobs.get(job_id)

    async def gen():
        if not job:
            yield f"data: {json.dumps({'event': 'error', 'message': 'unknown job'})}\n\n"
            return
        i = 0
        while True:
            async with job.cond:
                while i >= len(job.lines) and not job.done:
                    await job.cond.wait()
                batch = job.lines[i:]
                i = len(job.lines)
            for obj in batch:
                if obj.get("event") == "_end":
                    return
                yield f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
            if job.done and i >= len(job.lines):
                return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/result/{filename}")
def result(filename: str):
    p = OUTPUTS / Path(filename).name
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4")


@app.get("/api/output-meta/{filename}")
def output_meta(filename: str):
    p = OUTPUTS / Path(filename).name
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    m = _probe(p)
    m["size"] = p.stat().st_size
    m["name"] = p.name
    m["url"] = f"/api/result/{p.name}"
    m["createdAt"] = int(p.stat().st_mtime * 1000)
    return m


def _is_finished_reel(name: str) -> bool:
    # user-facing produced reels only — skip intermediates (reel_raw_/recap_raw_/
    # graded_1080_/src_/clean_/...)
    return name.endswith(".mp4") and "_raw_" not in name and (
        name.startswith("reel_") or name.startswith("recap_")
    )


@app.get("/api/library")
def library():
    """List finished reels in reel-studio-out (read-only), newest first."""
    items = []
    if OUTPUTS.exists():
        for p in OUTPUTS.glob("*.mp4"):
            if not _is_finished_reel(p.name):
                continue
            try:
                m = _probe(p)
            except Exception:
                m = {"width": 0, "height": 0, "fps": 0, "durationMs": 0}
            st = p.stat()
            items.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime * 1000),
                "width": m["width"],
                "height": m["height"],
                "fps": m["fps"],
                "durationMs": m["durationMs"],
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items}


@app.delete("/api/library/{filename}")
def library_delete(filename: str):
    p = OUTPUTS / Path(filename).name  # strip any path components
    if p.suffix != ".mp4" or not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        p.unlink()
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "deleted": p.name}


def _env_values() -> dict:
    """Parse the pipeline .env into {KEY: value}. Values stay SERVER-SIDE only."""
    out: dict = {}
    try:
        for line in ENV_PATH.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


@app.get("/api/settings/keys")
def settings_keys():
    """Report ONLY whether each key is set — never the value. Read-only."""
    vals = _env_values()
    return {
        "editable": False,  # keys are read-only; edit vex/.env by hand
        "envPath": str(ENV_PATH),
        "keys": [{"name": k, "present": bool(vals.get(k, "").strip())} for k in KEY_NAMES],
    }


@app.get("/api/settings/defaults")
def settings_defaults_get():
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_PATH.read_text())}
        except Exception:
            pass
    return DEFAULT_SETTINGS


@app.put("/api/settings/defaults")
async def settings_defaults_put(body: dict):
    cur = {}
    if SETTINGS_PATH.exists():
        try:
            cur = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            cur = {}
    merged = {**DEFAULT_SETTINGS, **cur}
    for k in ("captionEngine", "captionStyle", "bottomPercent", "recipe", "lufsDisplay"):
        if k in body:
            merged[k] = body[k]
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
    return merged


@app.get("/api/settings/output-dir")
def settings_output_dir():
    return {"path": str(OUTPUTS)}


# ------------------------------------------------------------------ B-roll --
# On-demand, OPT-IN B-roll suggestion (Phase B2): LLM moment-selection -> over-fetch from
# Pexels+Pixabay -> heuristic rank -> approve in the editor -> live preview. This path does
# NOT touch the pipeline (recaption/regenerate/ingest unchanged) and does NOT bake B-roll
# into any export yet (that's Phase B3). API keys live only in the vex-venv helpers; the
# server never reads or returns key values.
async def _run_vex_json(script: Path, payload: dict, timeout: float = 90.0) -> dict:
    """Spawn a vex-venv helper synchronously; return the last stdout line parsed as JSON."""
    pp = JOBS_TMP / f"broll_{uuid.uuid4().hex[:10]}.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = await asyncio.create_subprocess_exec(
        str(PIPELINE_PY), str(script), str(pp),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": f"{script.name} timed out"}
    finally:
        try:
            pp.unlink()
        except OSError:
            pass
    lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return {"error": f"{script.name} produced no output", "detail": err.decode('utf-8', 'replace')[-300:]}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"error": f"{script.name} bad output", "detail": lines[-1][:300]}


def _host_is_public(hostname: str) -> bool:
    norm = hostname.strip().strip("[]").lower()
    if norm in {"localhost", "localhost.localdomain"} or norm.endswith(".localhost"):
        return False
    try:
        addrs = [ipaddress.ip_address(norm)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(norm, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    return all(
        not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
             or a.is_reserved or a.is_unspecified)
        for a in addrs
    )


def _safe_https(url: str) -> str:
    """SSRF guard for downloading provider media: https-only, public host, no creds."""
    p = urllib.parse.urlparse(str(url or "").strip())
    if p.scheme.lower() != "https":
        raise ValueError("media URL must be https")
    if p.username or p.password:
        raise ValueError("media URL must not include credentials")
    host = (p.hostname or "").lower()
    if not host or not _host_is_public(host):
        raise ValueError(f"refusing non-public media host: {host or '?'}")
    return urllib.parse.urlunparse(p)


# Validate the ACTUAL socket peer IP after connect (and after every redirect), not just a
# fresh DNS lookup — a rebinding domain resolves "public" at check time and loopback/internal
# at connect time, so we must inspect the real connected peer to close that TOCTOU/SSRF hole.
class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        try:
            peer = self.sock.getpeername()[0]
        except OSError:
            peer = ""
        if not _host_is_public(peer):
            self.close()
            raise ValueError(f"refusing connection to non-public peer: {peer or '?'}")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req)


class _RedirGuard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _safe_https(newurl)  # re-run the https/public-host string check on the redirect target
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_broll(url: str, dest: Path) -> None:
    safe = _safe_https(url)
    req = urllib.request.Request(safe, headers={"User-Agent": "ReelStudio/1.0"})
    # HTTPSOnlyRedirect + peer-IP-pinned connection = redirects are re-validated AND every
    # real socket (initial + redirects) must land on a public IP.
    opener = urllib.request.build_opener(_PinnedHTTPSHandler(), _RedirGuard())
    # Unique temp per download so two concurrent fetches of the same key can't interleave
    # writes into one .part file; atomic rename gives last-writer-wins a complete file.
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.part")
    try:
        with opener.open(req, timeout=60) as resp:
            _safe_https(resp.geturl())
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > MAX_BROLL_BYTES:
                raise ValueError("media larger than cache limit")
            total = 0
            with tmp.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BROLL_BYTES:
                        raise ValueError("media larger than cache limit")
                    f.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


_MEDIA_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp", ".mov": "video/quicktime"}


@app.post("/api/broll/plan")
async def broll_plan(body: dict):
    """LLM moment-selection over the resolved project's transcript (with per-word edits applied).
    Optional body.userClips = [{key, description, kind, durationMs}] — clips the user already
    uploaded via /api/broll/upload; the planner may assign one to a matching scene moment
    instead of searching stock (see broll_plan.py's assignedUserClipKey)."""
    edits = body.get("edits", {}) or {}
    pid = body.get("project")
    proj, _ = _resolve(pid)
    wj = _reconstruct_transcript(edits.get("editedText", {}), pid)
    words = _build_words(wj)
    dur = _probe(Path(proj["video"]))["durationMs"] or (words[-1]["endMs"] + 2000 if words else 0)
    user_clips = [
        {"key": str(c.get("key")), "description": str(c.get("description") or "").strip()}
        for c in (body.get("userClips") or [])
        if isinstance(c, dict) and c.get("key") and str(c.get("description") or "").strip()
    ]
    data = await _run_vex_json(BROLL_PLAN, {"words": words, "durationMs": dur, "userClips": user_clips})
    if data.get("error"):
        return JSONResponse(data, status_code=502)
    if isinstance(data.get("moments"), list):  # stable server-assigned id per moment
        for i, m in enumerate(data["moments"]):
            if isinstance(m, dict) and not m.get("momentId"):
                m["momentId"] = f"m{i}"
    return data


@app.post("/api/zoom/plan")
async def zoom_plan(body: dict):
    """Auto-zoom moment selection from TWO signals (LLM meaning + ffmpeg/numpy audio-energy
    peaks) over the resolved project's base + transcript. On-demand; does not touch the pipeline."""
    edits = body.get("edits", {}) or {}
    pid = body.get("project")
    proj, _ = _resolve(pid)
    wj = _reconstruct_transcript(edits.get("editedText", {}), pid)
    words = _build_words(wj)
    dur = _probe(Path(proj["video"]))["durationMs"] or (words[-1]["endMs"] + 2000 if words else 0)
    data = await _run_vex_json(
        ZOOM_PLAN,
        {"words": words, "durationMs": dur, "baseVideo": str(proj["video"])},
        timeout=120.0,  # audio decode + LLM
    )
    if data.get("error"):
        return JSONResponse(data, status_code=502)
    if isinstance(data.get("zooms"), list):  # stable id + startMs/endMs aliases for the FE
        for i, z in enumerate(data["zooms"]):
            if isinstance(z, dict):
                z.setdefault("zoomId", f"z{i}")
                z.setdefault("startMs", z.get("spanStartMs"))
                z.setdefault("endMs", z.get("spanEndMs"))
    return data


@app.post("/api/chat")
async def chat(body: dict):
    """Fine-tune's chat sheet — one LLM call per turn returning structured intent, NOT an agentic
    execution loop (see chat_agent.py's docstring for why: every mutation here is a client-side
    React state change, so there's nothing server-side to execute). `state` is a snapshot the
    CLIENT builds of its own current moments/toggles/sfx/music — this endpoint is a thin,
    type-coercing pass-through to chat_agent.py, which does the real validation against that
    snapshot (never trusts the model's ids/enums blindly)."""
    message = str(body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    history = [
        {"role": str(h.get("role")), "content": str(h.get("content") or "")}
        for h in (body.get("history") or []) if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ]
    state = body.get("state") if isinstance(body.get("state"), dict) else {}
    data = await _run_vex_json(CHAT_AGENT, {"message": message, "history": history, "state": state}, timeout=45.0)
    if data.get("error"):
        return JSONResponse(data, status_code=502)
    return data


@app.post("/api/broll/fetch")
async def broll_fetch(body: dict):
    """Over-fetch ranked candidates (videos+images, portrait-first) for one query."""
    query = str(body.get("query", "")).strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    payload = {
        "query": query,
        "page": int(body.get("page", 1) or 1),
        "spanMs": int(body.get("spanMs", 3000) or 3000),
        "orientation": str(body.get("orientation", "portrait")),
    }
    # 4 sequential provider requests @20s each -> allow up to 120s before killing the child
    # (was 90s < ~80-120s worst case, which killed a still-working fetch and dropped results).
    data = await _run_vex_json(BROLL_FETCH, payload, timeout=120.0)
    if data.get("error"):
        return JSONResponse(data, status_code=502)
    return data


# One in-flight download per cache key — dedups concurrent /cache calls for the same URL so
# they don't race on the same destination (unique .part + atomic rename handles the rest).
_broll_locks: dict[str, asyncio.Lock] = {}


@app.post("/api/broll/cache")
async def broll_cache(body: dict):
    """Download a provider media URL into the local cache (SSRF-guarded) and return a
    Range-capable local URL for Remotion. Idempotent (keyed by url hash)."""
    url = str(body.get("url", "")).strip()
    kind = str(body.get("kind", "video")).strip().lower()
    ext = ".mp4" if kind == "video" else ".jpg"
    try:
        _safe_https(url)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    key = f"{hashlib.sha1(url.encode()).hexdigest()[:16]}{ext}"
    dest = BROLL_CACHE / key
    lock = _broll_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if not dest.exists():
            try:
                await asyncio.get_event_loop().run_in_executor(None, _download_broll, url, dest)
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"error": f"download failed: {str(e)[:160]}"}, status_code=502)
    return {"key": key, "url": f"/api/broll/media/{key}", "kind": kind, "size": dest.stat().st_size}


@app.post("/api/broll/upload")
async def broll_upload(request: Request, name: str = "clip.mp4", kind: str = "video"):
    """User-supplied B-roll (Mixkit/Coverr/screen-recording) for one span -> cache dir."""
    safe = Path(name).name or "clip"
    ext = Path(safe).suffix.lower() or (".mp4" if kind == "video" else ".jpg")
    key = f"up_{uuid.uuid4().hex[:12]}{ext}"
    dest = BROLL_CACHE / key
    total = 0
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BROLL_BYTES:  # cap disk use like the download path
                    dest.unlink(missing_ok=True)
                    return JSONResponse({"error": "upload too large"}, status_code=413)
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "empty upload"}, status_code=400)
    k = "image" if ext in {".jpg", ".jpeg", ".png", ".webp"} else "video"
    # durationMs lets the client bound a later retime slider without a separate probe round-trip.
    dur_ms = 0
    if k == "video":
        dur_ms = (await asyncio.get_event_loop().run_in_executor(None, _probe, dest)).get("durationMs", 0)
    return {"key": key, "url": f"/api/broll/media/{key}", "kind": k, "size": dest.stat().st_size,
            "durationMs": dur_ms}


@app.get("/api/broll/media/{key}")
def broll_media(key: str):
    p = _safe_child(BROLL_CACHE, key)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(p), media_type=_MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream"))


def _media_url_to_path(url: str) -> Path | None:
    """Resolve a /api/broll/media/{key} URL to its cached file, traversal-safe."""
    key = str(url or "").rsplit("/", 1)[-1]
    return _safe_child(BROLL_CACHE, key)


@app.post("/api/broll/render")
async def broll_render(body: dict):
    """B3: bake the accepted B-roll (+ captions) into a NEW exported reel via approach A —
    ONE Remotion composite -> the SAME pro-export + loudnorm + QC the normal reel uses. This
    is on-demand/opt-in and does NOT touch run_pipeline/recaption_only/ingest. Media comes
    from the local B2 cache (no network at render time)."""
    accepted_in = body.get("accepted") or []
    zooms_in = body.get("zooms") or []
    if not isinstance(accepted_in, list):
        accepted_in = []
    # sanitize the auto-zoom spans (pure transform on the base — deterministic, no media/network)
    zooms: list[dict] = []
    for z in zooms_in if isinstance(zooms_in, list) else []:
        try:
            zooms.append({
                # accept either the render shape (startMs) or raw zoom_plan (spanStartMs)
                "startMs": int(z.get("startMs", z.get("spanStartMs"))),
                "endMs": int(z.get("endMs", z.get("spanEndMs"))),
                "style": (z.get("style") if z.get("style") in ("quick_punch", "seam_mask")
                          else "slow_push"),
                # Ceiling raised 1.4 -> 1.6 to match the researched punch-in ladder's "Critical"
                # tier (zoom_plan.py's SCALE_PUNCH now reaches up to 1.48) -- the old 1.4 cap was
                # silently clipping the strongest, most-deserved punches back down to weak.
                "targetScale": max(1.0, min(1.6, float(z.get("targetScale", 1.2)))),
                # face (default) | center -- see the anchor branch in render_broll_ffmpeg
                "anchor": "center" if z.get("anchor") == "center" else "face",
            })
        except (KeyError, TypeError, ValueError):
            continue
    # Only when the user has zoom ON. The client sends an empty list when Auto-zoom is switched
    # off, so adding seam masks unconditionally re-introduced zooms the user had explicitly
    # disabled -- an empty zooms list is a decision, not an absence of one.
    if zooms_in:
        zooms += _seam_mask_zooms(zooms, body.get("project"))
    accepted: list[dict] = []
    for item in accepted_in:
        base = {
            "momentId": str(item.get("momentId") or ""),
            "startMs": int(item.get("startMs", 0)),
            "endMs": int(item.get("endMs", 0)),
        }
        if item.get("kind") == "card":
            # Graphic card — text only (deterministic), no cache lookup / no network / no file.
            card = item.get("card") or {}
            items = card.get("items")
            accepted.append({
                **base, "kind": "card",
                "card": {
                    "cardType": str(card.get("cardType") or "phrase"),
                    "headline": str(card.get("headline") or ""),
                    "value": (str(card["value"]) if card.get("value") else None),
                    "items": ([str(x) for x in items][:4] if isinstance(items, list) else None),
                    "style": str(card.get("style") or "ink"),
                },
            })
            continue
        # stock media (kind "media"): resolve the cached local file for its url
        path = _media_url_to_path(str(item.get("url", "")))
        if path is None:
            return JSONResponse(
                {"error": f"B-roll media not in cache for moment {item.get('momentId')!r}"},
                status_code=400,
            )
        is_video = item.get("mediaKind") == "video"
        entry = {**base, "kind": "video" if is_video else "image", "path": str(path)}
        if is_video:
            # Retime (Fine-tune's B-roll trim slider) — which in-point of the SOURCE clip to
            # start from; 0 (the default) is the original, unmodified behavior. Negative values
            # would seek backwards from a clamped point, so floor at 0 here.
            entry["sourceStartMs"] = max(0, int(item.get("sourceStartMs", 0) or 0))
        accepted.append(entry)

    settings = body.get("captionSettings", {}) or {}
    edits = body.get("edits", {}) or {}
    pid = body.get("project")
    proj, _ = _resolve(pid)
    wj = _reconstruct_transcript(edits.get("editedText", {}), pid)

    # Base video for the bake. Normally the project's current base. But the Look-picker Apply
    # flow used to rely on an async regrade job swapping projects[pid]["video"] BEFORE this
    # render read it -- a race that intermittently baked the reel on the OLD, ungraded base
    # (observed: an explicitly-graded regrade file existed, yet the rendered reel had zero
    # color change). To make it deterministic, the client passes the exact regraded file it
    # just produced as `base_override`; we use that instead of the mutable pointer. Resolved
    # strictly by basename inside the uploads dir so it can't reference an arbitrary path.
    base_video = str(proj["video"])
    override = body.get("base_override")
    if override:
        cand = UPLOADS / Path(str(override)).name
        if cand.exists() and cand.suffix == ".mp4":
            base_video = str(cand)

    # Sound effects (Fine-tune's SFX sheet) — each hit resolves to one of the fixed catalog
    # sounds (never an arbitrary path) placed at a clamped timestamp with a clamped gain.
    sfx_hits: list[dict] = []
    sfx_in = body.get("sfxHits") or []
    if sfx_in:
        base_dur_ms = _probe(Path(proj["video"]))["durationMs"] if proj.get("video") else 0
        for h in sfx_in:
            if not isinstance(h, dict):
                continue
            sp = _sfx_id_to_path(h.get("soundId"))
            if sp is None:
                continue
            at_ms = max(0, int(h.get("atMs", 0) or 0))
            if base_dur_ms:
                at_ms = min(at_ms, max(0, base_dur_ms - 50))
            gain_db = max(-24.0, min(6.0, float(h.get("gainDb", 0.0) or 0.0)))
            # user gain rides ON TOP of the level match, so the slider means the same relative
            # thing for every sound instead of depending on how that file happened to be mastered
            sfx_hits.append({"path": str(sp), "atMs": at_ms,
                             "gainDb": round(gain_db + _sfx_makeup_db(sp), 2)})

    # Music (Fine-tune's Music sheet) — resolves to either a bundled catalog track id or one of
    # THIS project's own uploaded keys, never an arbitrary path. trackId is required; everything
    # else falls back to a sensible default so the client only has to send what the user changed.
    music_payload: dict | None = None
    music_in = body.get("music")
    if isinstance(music_in, dict) and music_in.get("trackId"):
        mp = _music_path(str(music_in["trackId"]))
        if mp is not None:
            gain_db = max(-24.0, min(6.0, float(music_in.get("gainDb", -12.0) or -12.0)))
            ducking = str(music_in.get("ducking", "medium"))
            if ducking not in music_catalog.DUCKING_PRESETS:
                ducking = "medium"
            music_payload = {"path": str(mp), "gainDb": gain_db, "ducking": ducking}

    # Nothing to bake only if there are no overlays, no zooms, no SFX, no music AND captions are
    # off too (captions-only is a valid reel — the bake renders base + captions). Checked against
    # the SANITIZED sfx_hits/music_payload (not the raw request body) so a bad soundId/trackId
    # that resolves to nothing doesn't slip past this guard and spawn a doomed async job instead
    # of failing here with an immediate, actionable 400.
    captions_off = bool(body.get("captionsOff"))
    if not accepted and not zooms and not sfx_hits and not music_payload and captions_off:
        return JSONResponse(
            {"error": "nothing to bake (no B-roll, no cards, no zooms, no SFX, no music)"}, status_code=400
        )

    job_id = uuid.uuid4().hex[:12]
    tj = JOBS_TMP / f"{job_id}.transcript.json"
    tj.write_text(json.dumps(wj, ensure_ascii=False))
    out_name = f"reel_broll_{job_id[:6]}.mp4"
    payload = {
        "base_video": base_video,
        "transcript_json_path": str(tj),
        "output_path": str(OUTPUTS / out_name),
        "accepted_broll": accepted,
        "zooms": zooms,
        "bottom_percent": settings.get("bottomPercent", 22),
        "style": settings.get("styleId", settings.get("style", "word-focus")),
        "size_px": settings.get("sizePx"),  # mobile caption-style override (ignored by Remotion path)
        "captions_off": bool(body.get("captionsOff")),  # mobile can bake overlays w/o captions
        # "Smooth cutaways" toggle — alpha-fades B-roll/Ken-Burns overlay entry+exit instead of
        # an instant hard-cut pop. Read by the ffmpeg path only (ignored by the Remotion path,
        # same as size_px above); default True matches the Setup CATALOG toggle's default-on.
        "smoothTransitions": bool(body.get("smoothTransitions", True)),
        # SFX/music only affect the ffmpeg path (render_broll_ffmpeg.py); ignored by the Remotion
        # path the same way size_px/smoothTransitions already are (see the fields above).
        "sfxHits": sfx_hits,
        "music": music_payload,
        "fps": 60,
        "run_qc": True,
    }
    # Renderer choice: default "remotion" (desktop, keeps live preview==export). Mobile passes
    # engine="ffmpeg" -> the Remotion-free pure-ffmpeg bake (no headless Chrome, no license).
    ff = str(body.get("engine", "remotion")).lower() == "ffmpeg"
    mode = "broll_render_ffmpeg" if ff else "broll_render"
    jobs[job_id] = Job()
    pp = JOBS_TMP / f"{job_id}.payload.json"
    pp.write_text(json.dumps(payload, ensure_ascii=False))
    asyncio.create_task(_run_job(job_id, mode, pp))
    return {"job_id": job_id, "output_name": out_name, "resultUrl": f"/api/result/{out_name}",
            "brollCount": len(accepted), "zoomCount": len(zooms), "sfxCount": len(sfx_hits),
            "hasMusic": music_payload is not None, "engine": "ffmpeg" if ff else "remotion"}


@app.post("/api/publish-kit")
async def publish_kit(project: Optional[str] = None):
    """Spawn the VEX venv Python to draft a title/description/hashtags from the ACTIVE
    transcript via OpenAI (gpt-4o-mini). Never logs or returns the API key.

    Takes an optional `project` id (same `_resolve()` pattern as every other route below)
    so concurrent mobile users each get a publish-kit drafted from THEIR OWN transcript —
    previously this always read the legacy global ACTIVE project regardless of which
    project the caller actually meant, which would silently return the wrong reel's
    caption/hashtags for any non-default project."""
    proj, _ = _resolve(project)
    wj = json.loads(Path(proj["transcript"]).read_text())
    segs = wj.get("segments", []) if isinstance(wj, dict) else wj
    text = " ".join(str(s.get("text", "")) for s in (segs or [])).strip()
    if not text:
        text = " ".join(
            str(w.get("word") or w.get("text") or "")
            for s in (segs or [])
            for w in s.get("words", [])
        ).strip()
    tf = JOBS_TMP / f"pubkit_{uuid.uuid4().hex[:8]}.txt"
    tf.write_text(text)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = await asyncio.create_subprocess_exec(
        str(PIPELINE_PY), str(PUBKIT), str(tf),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    out, err = await proc.communicate()
    try:
        tf.unlink()
    except OSError:
        pass
    try:
        data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        return JSONResponse(
            {"error": "publish-kit failed", "detail": err.decode("utf-8", "replace")[-300:]},
            status_code=500,
        )
    if data.get("error"):
        return JSONResponse(data, status_code=502)
    return data
