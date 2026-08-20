# Reel Studio — Concurrent-User Handling

How the backend safely serves **many users at once on a single machine**, and what devops must
know. No Redis, no auth — by design.

---

## TL;DR

- **One async process** handles many simultaneous users; the real bottleneck is the video
  pipeline, which we **cap with a queue** — not the web layer.
- Each upload gets a **`project_id`**; every later call carries it, so concurrent users can't
  read or overwrite each other's work.
- The server keeps job/project state **in memory** → it **must run as ONE uvicorn worker**.
- Everything is **additive & backward-compatible**; nothing here needs external infra.

---

## 1. The problem it solves

The backend was originally **single-tenant**: one global `ACTIVE` project variable that every
upload overwrote. With two users, the second upload clobbered the first, so the first user's
render silently used the *other* user's video — a cross-user data leak, not just a glitch.

## 2. The model: one process, isolated state

One async process serves many users at once (it handles other requests while a render runs). The
bottleneck is the video pipeline, capped by a job queue — not the web layer. All job/project state
lives in this one process's memory, so **no Redis is required** — until one machine isn't enough
(see §5).

---

## 3. The five mechanisms

### (1) Per-project isolation — `project_id`
- `POST /api/upload` mints a `project_id` (reuses the job id) and returns it.
- The client stores it and sends `project=<id>` on **every** later call
  (`/api/project`, `/api/broll/plan`, `/api/zoom/plan`, `/api/broll/render`, …).
- The server keeps `projects: dict[project_id → {video, transcript, source, version}]`.
  `_resolve(project_id)` returns that user's entry; ingest writes `projects[pid]`.
- The legacy global `ACTIVE` remains **only** as a fallback for id-less callers (the desktop
  editor), so nothing broke.

```
User A upload → project_id "A"      User B upload → project_id "B"
      │                                    │
      ▼                                    ▼
projects["A"] = {A's video…}       projects["B"] = {B's video…}   (ACTIVE = last, but nobody reads it)
      │                                    │
A's render?project=A → A's video   B's render?project=B → B's video   ✓ isolated
```

*Files:* `server.py` (`projects`, `_resolve`, `_activate_ingest`); `src/mobile/api.ts` + `MobileApp.tsx` (threads the id).

### (2) Job queue — admission control
Every heavy job (ingest/render) spawns a full pipeline subprocess (Whisper model + ffmpeg
grabbing all cores). Unbounded, ~10 at once would OOM/thrash the box and take everyone down.

- A lazily-created `asyncio.Semaphore(MAX_CONCURRENT_JOBS)` gates the subprocess spawn.
- The async task is created **instantly** (SSE progress connects), but the heavy work waits for a
  free slot → a FIFO queue with a hard ceiling. Queued users see a "Queued — waiting for a free
  slot…" message.
- Side benefit: it also keeps us under the shared transcription API's concurrency limit.

*File:* `server.py` (`_job_sem()`, `_run_job`).

### (3) Own-transcript ingest
Ingest used to grab "the newest file" from a **shared** projects dir — two concurrent ingests
could cross transcripts. Now `run_pipeline(on_project=…)` reports the exact project dir it
created, and ingest reads *that* file. No shared-dir race.

*Files:* `reel-studio/pipeline.py` (additive `on_project` callback); `backend/pipeline_runner.py`.

### (4) Upload size cap
`POST /api/upload` counts bytes as they stream and aborts with **413** (deleting the partial)
past `MAX_UPLOAD_MB`, so one huge/looping upload can't fill the shared disk for everyone.

### (5) Cleanup janitor
A background sweep (every ~60s) drops **finished jobs** from memory after `JOB_TTL_MIN`, unlinks
their scratch files, and ages out **unreferenced** uploads after `FILE_TTL_MIN` (never deletes a
live project's files). Prevents unbounded memory/disk growth.

*File:* `server.py` (`_janitor`, `_in_use_paths`, startup hook).

---

## 4. Tunable env vars (devops sets these per machine)

| Var | Default | Meaning |
|---|---|---|
| `MAX_CONCURRENT_JOBS` | `2` | Heavy pipeline jobs allowed at once |
| `MAX_UPLOAD_MB` | `500` | Max upload size before 413 |
| `JOB_TTL_MIN` | `15` | How long finished jobs stay in memory |
| `FILE_TTL_MIN` | `120` | How long unreferenced scratch/uploads stay on disk |

**Sizing `MAX_CONCURRENT_JOBS`:** it's a dial the deployer owns. Higher needs more CPU/RAM **and**
a transcription plan that allows the concurrency. Rule of thumb: `min(what CPU/RAM can handle,
what the STT plan allows)`. Rendering is CPU-bound, so past the core count more concurrency makes
each job *slower*, not the throughput higher. Start at 2–4, measure, raise.

---

## 5. ⚠️ The rule for devops: single worker

All job/project state lives in **this process's memory**. Therefore:

> **Run exactly one uvicorn worker / one replica** (`--workers 1`).

With `--workers 2+` or multiple pods, a job created in worker A is invisible to worker B (SSE
returns "unknown job") and each worker has its own project store. This is stated as a comment in
`server.py`.

### When you need Redis (not now)
Only if a **single machine can't keep up**. Since rendering is CPU-bound, "more users" is first
solved by a **bigger machine**, not more workers. The day you truly need multiple machines is the
day you move `jobs` + `projects` + SSE to **Redis + a real job queue (Celery/RQ)** — a larger
rewrite, explicitly out of current scope.

---

## 6. Already concurrency-safe (don't touch)

- **Per-job state** — `jobs` keyed by unique `uuid4`; each has its own `asyncio.Condition`.
- **B-roll downloads** — per-URL lock + write-to-`.part`-then-atomic-`replace`; no torn files.
- **Output filenames** — namespaced by job/uuid; no path collisions.
- **B-roll cache** — content-addressed by `sha1(url)`; safe across users.
- **The `ACTIVE` swap** — asyncio is single-threaded, so the per-project fix needs **no locks**.

---

## 7. Python 3.9 gotchas (this venv is 3.9.6)

Both of these crash the server at runtime and were fixed — **don't reintroduce them:**

1. **No `X | None` in FastAPI endpoint parameter annotations.** FastAPI evaluates them at import;
   3.9 raises `unsupported operand type(s) for |`. Use `typing.Optional[...]`.
   (`from __future__ import annotations` does **not** help — FastAPI force-evaluates.)
2. **No module-level `asyncio.Semaphore`/`Lock`/`Condition`.** 3.9 binds them to the import-time
   loop; under uvicorn's loop they fail with "got Future attached to a different loop". Create
   them **lazily inside the running loop** (see `_job_sem()`).

---

## 8. How to test concurrency

- **Queue:** set `MAX_CONCURRENT_JOBS=1`, open two browsers, start editing in both → the second
  shows "Queued…" then runs after the first.
- **Isolation:** upload a *different* clip in each browser → each reel comes out with its own
  video/captions, no cross-over. (This is the fix that matters most.)
- **Unit proof (no API keys needed):** the isolation logic is directly testable — ingest project
  A, then B, and confirm `_resolve("A")` still returns A's video/transcript even though `ACTIVE`
  is now B.
