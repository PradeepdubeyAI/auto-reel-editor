# Reel Studio — API Reference

Backend on **:8000** (dev: via the Vite proxy at `/api`). CORS open. Single worker.

## Rules

- **`project_id`** — returned by `/api/upload`; send it on every later call (`?project=` for GET,
  `"project"` in the body for POST). It keeps concurrent users isolated.
- **`job_id`** — returned by slow calls (upload, render); stream progress at `/api/progress/{job_id}`.
- Responses may include extra fields (marked ✨). Clients ignore unknown fields.

## Flow (order the app calls them)

```
POST /api/upload            → project_id, job_id      (+ watch /api/progress/{job_id})
GET  /api/project?project=  → words[] for the editor
GET  /api/caption-styles    → style picker
POST /api/broll/plan        → moments[]
POST /api/zoom/plan         → zooms[]
POST /api/broll/fetch       → candidates[]   (per moment)
POST /api/broll/cache       → local url      (per chosen clip)
POST /api/broll/render      → output_name, resultUrl  (+ watch /api/progress/{job_id})
GET  /api/result/{name}     → the reel (play/download)
GET  /api/output-meta/{name}→ real size/length for the Ready screen
```

### Step by step — when each call fires and how its payload feeds the next

`PID` below = the `project_id` returned in step 1. `edits` = `{ editedText: { "w12":"fix" } }` (any word corrections, or `{}`).

**1. Upload** — `POST /api/upload` · *when the user picks a video.*
- Send: the video bytes + query `name, clean_audio, remove_silences, color_grade`.
- Get: `project_id` (**keep as PID**) and `job_id`.
- Feeds next: open progress with `job_id`; attach PID to every call from here on.

**2. Watch "prepare"** — `GET /api/progress/{job_id}` · *immediately after upload.*
- Get: a stream of `step`/`log`, ending in `done`.
- Feeds next: on `done`, the base clip + transcript exist → fetch words and styles.

**3. Words** — `GET /api/project?project=PID` · *after prepare finishes.*
- Get: `words[] = {id,text,startMs,endMs}`.
- Feeds next: render the tap-to-fix caption chips; edited words become `edits.editedText` in step 9.

**4. Styles** — `GET /api/caption-styles` · *when the Setup screen opens.*
- Get: `styles[]`, `defaultStyleId`, `sizePresets`, `position`.
- Feeds next: the chosen style/size/position become `captionSettings` in step 9.

**5. Plan B-roll** — `POST /api/broll/plan` · body `{ project: PID, edits }` · *when the user hits "Start editing".*
- Get: `moments[]`, each with `momentId`, `spanStartMs/spanEndMs`, `type`, `primaryQuery`.
- Feeds next: for each `scene` moment, search stock using its `primaryQuery` (step 7); `momentId`/spans carry into step 9's `accepted[]`.

**6. Plan zooms** — `POST /api/zoom/plan` · body `{ project: PID, edits }` · *same step as 5.*
- Get: `zooms[] = {startMs,endMs,style,targetScale,…}`.
- Feeds next: pass the array straight into step 9's `zooms`.

**7. Find clips** — `POST /api/broll/fetch` · body `{ query: moment.primaryQuery, spanMs: end−start, orientation:"portrait" }` · *once per B-roll moment.*
- Get: `candidates[] = {mediaUrl,thumbUrl,kind,…}`.
- Feeds next: pick one (e.g. `candidates[0]`) and download it (step 8).

**8. Save the clip** — `POST /api/broll/cache` · body `{ url: candidate.mediaUrl, kind: candidate.kind }` · *after a candidate is chosen.*
- Get: `{ url }` — a local link like `/api/broll/media/…`.
- Feeds next: put that `url` into step 9's `accepted[]` entry for this `momentId`.

**9. Make the reel** — `POST /api/broll/render` · *when planning + clip selection are done.* Body assembles everything above:
```json
{ "project": "PID",
  "accepted": [ { "momentId":"m0", "startMs": <moment.spanStartMs>, "endMs": <moment.spanEndMs>,
                  "mediaKind": <candidate.kind>, "url": <step-8 url> } ],
  "zooms": <step-6 zooms[]>,
  "captionSettings": { "styleId": <step-4 pick>, "sizePx": …, "bottomPercent": … },
  "edits": <step-3 edits>,
  "captionsOff": false, "engine": "ffmpeg" }
```
- Get: `output_name`, `resultUrl`, and a new `job_id`.
- Feeds next: open progress again with the new `job_id` (like step 2); read `qc[]` from its `done` event.

**10. Play / download** — `GET /api/result/{output_name}` · *Ready screen.* → the reel video (set as `<video src>` and the download link).

**11. Real details** — `GET /api/output-meta/{output_name}` · *Ready screen.* → true `width/height/durationMs` to display instead of a guess.

> Fine-tune re-uses this: swapping a clip = another `broll/cache`; changing captions/toggles/word-fixes = call `broll/render` again with the new `accepted`/`captionSettings`/`edits`. No re-upload.

---

## Endpoints

### `POST /api/upload`
Use when the user picks or drops a video — it uploads the file, starts preparing the clip, and hands back the `project_id` that ties every later call together.
- **Send:** video bytes (body); query `name, clean_audio, remove_silences, color_grade`.
- **Returns:** `project_id`, `job_id`, `name`, `sourceDurationMs`✨, `sourceSizeBytes`✨, `createdAt`✨ (epoch ms).
- **Errors:** `413` too large (> `MAX_UPLOAD_MB`), `400` empty.

### `GET /api/progress/{job_id}` — SSE
Open this right after a slow job (upload or render) to get live status for the progress screen; it ends with a `done` or `error` message. Consume with `EventSource`; stop on `done`/`error`.

| Event | Payload | Meaning |
|---|---|---|
| `step` | `name, status` | a stage started |
| `log` | `text` | status line (incl. "Queued — waiting for a free slot…") |
| `done` | `output, qc[]` | success |
| `error` | `message` | failure |

Server replays the full backlog on connect (no `Last-Event-ID` needed). `"unknown job"` = lost → retry.

### `GET /api/project?project={id}`
Call this once the clip is prepared to get the transcript — the spoken words with timings that power the tap-to-fix caption editor.
- **Returns:** `name, videoUrl, fps, width, height, durationMs, words[]`.
- `words[]` = `{ id, text, startMs, endMs }` — used by the caption word-editor.

### `GET /api/caption-styles`
Call this once when the Setup screen opens to load every caption look, so the app can show the style picker and its live preview.
- **Returns:** `styles[]` (id, label, colours, size, outline, glow, box, motion flags), `sizePresets`, `position{min,max}`, `defaultStyleId`. Drives the picker + preview.

### `POST /api/broll/plan`
Call this when editing starts — the AI reads the transcript and returns the moments that should get a stock clip or a text graphic.
- **Send:** `{ project, edits:{editedText} }`
- **Returns:** `moments[]`:

| Field | Meaning |
|---|---|
| `momentId` ✨ | stable id (swap/remove) |
| `spanStartMs`, `spanEndMs` | when the cutaway plays |
| `type` | `scene` (stock) / `text_or_stat` (graphic) / `abstract` |
| `primaryQuery` | stock search phrase |
| `fallbackQueries` | alternate searches |
| `transcriptPhrase` | words spoken then |
| `card` | graphic content: `cardType, headline, value, items` |

- **Errors:** `502` provider failure.

### `POST /api/zoom/plan`
Call this alongside B-roll planning — the AI returns the moments to punch-in (zoom) for emphasis.
- **Send:** `{ project, edits:{editedText} }`
- **Returns:** `zooms[]`: `zoomId`✨, `startMs`/`endMs`✨ (+ original `spanStartMs`/`spanEndMs`), `style` (`slow_push`/`quick_punch`), `targetScale`, `reason`, `transcriptPhrase`.

### `POST /api/broll/fetch`
Call this for each B-roll moment to search Pexels/Pixabay and get back matching stock clips to choose from. (Stateless — no `project`.)
- **Send:** `{ query, page, spanMs, orientation:"portrait" }`
- **Returns:** `candidates[]`: `id, source, kind, thumbUrl, mediaUrl, width, height, orientation, durationSec, creator, sourceUrl`. (`creator`/`sourceUrl` = attribution.)

### `POST /api/broll/cache`
Call this after a clip is chosen to download it onto our server so it's ready for the reel; you get back a local link to use in the render.
- **Send:** `{ url, kind:"video"|"image" }`
- **Returns:** `{ key, url, kind, size }` — use `url` as the clip's `url` in the render payload.

### `POST /api/broll/render`
Call this to build the final reel from everything chosen (clips, zooms, captions, word fixes); it returns the finished file name + link and streams progress like the upload did.
- **Send:**
```json
{ "project": "<id>",
  "accepted": [ {"momentId","startMs","endMs","mediaKind","url"},
                {"momentId","startMs","endMs","kind":"card","card":{…}} ],
  "zooms": [ {"startMs","endMs","style","targetScale"} ],
  "captionSettings": { "styleId","sizePx","bottomPercent" },
  "edits": { "editedText": {"w12":"fixed"} },
  "captionsOff": false, "engine": "ffmpeg" }
```
- **Returns:** `job_id, output_name, resultUrl`✨, `brollCount, zoomCount, engine`. `qc[]` arrives via the SSE `done` event.

### `GET /api/result/{name}`
Use this URL to play or download the finished reel (it's the `<video>` source and the download link). Serves `video/mp4` with Range (scrubbing).

### `GET /api/output-meta/{name}`
Call this on the Ready screen to get the reel's real size and length, so you show true numbers instead of a guess.
- **Returns:** `width, height, fps, durationMs, size, name`✨, `url`✨, `createdAt`✨.

---

## Complete route list (every backend route)

App = used by the phone app · indirect = server-internal · desktop = desktop editor only.

| Route | Who | Purpose |
|---|---|---|
| `POST /api/upload` | app | upload + start prepare |
| `GET /api/progress/{job_id}` | app | live progress (SSE) |
| `GET /api/project` | app | words for the editor |
| `GET /api/caption-styles` | app | caption style picker |
| `POST /api/broll/plan` | app | pick B-roll moments |
| `POST /api/zoom/plan` | app | pick zoom moments |
| `POST /api/broll/fetch` | app | stock search |
| `POST /api/broll/cache` | app | download chosen clip |
| `POST /api/broll/render` | app | make the reel |
| `GET /api/result/{name}` | app | play/download the reel |
| `GET /api/output-meta/{name}` | app | reel size/length |
| `GET /api/broll/media/{key}` | indirect | serves the cached clip to the renderer |
| `GET /api/health` | ops | liveness check |
| `GET /api/video` | desktop | base mp4 preview |
| `GET /api/transcript` | desktop | raw transcript JSON |
| `POST /api/recaption` | desktop | captions-only re-render |
| `POST /api/regenerate` | desktop | full pipeline re-run |
| `GET /api/library` | desktop | list finished reels |
| `DELETE /api/library/{name}` | desktop | delete a reel |
| `GET /api/settings/keys` | desktop | which API keys are present |
| `GET /api/settings/defaults` | desktop | default recipe/caption settings |
| `PUT /api/settings/defaults` | desktop | save defaults |
| `GET /api/settings/output-dir` | desktop | output directory path |
| `POST /api/broll/upload` | desktop | user-supplied B-roll into cache |
| `POST /api/publish-kit` | desktop | title/description/hashtags helper |

**25 routes total** — 11 used by the app (detailed above), 1 indirect, 13 desktop/ops.

## Errors

| Code / event | Meaning | Show |
|---|---|---|
| `413` | upload too big | "File too large (max 500 MB)" |
| `502` | provider failed | "Try again" |
| `404` (result) | reel missing | re-render |
| `log` "Queued…" | waiting for a slot | Queued state |
| `error` "unknown job" | session lost | retry |

## Not built
No auth, billing, share, home feed, or prompt-to-reel endpoints — out of scope. See [ARCHITECTURE.md](./ARCHITECTURE.md).
