# Testing the API in Postman

Test straight against the backend at **`http://localhost:8000`** (not the Vite proxy).

---

## 0. Start the backend

```bash
cd video-edit-mobile/reel-studio-ui/backend
set -a; . ../../vex/.env; set +a
./.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
```
Check it's up: open `http://localhost:8000/api/health` in a browser → `{"ok":true,…}`.

---

## 1. One-time Postman setup

1. **Create an Environment** (top-right → "Environments" → +). Name it `reel-local`, add:

   | Variable | Initial value |
   |---|---|
   | `baseUrl` | `http://localhost:8000` |
   | `projectId` | *(leave blank)* |
   | `jobId` | *(leave blank)* |
   | `outputName` | *(leave blank)* |
   | `mediaUrl` | *(leave blank)* |

   Select this environment (top-right dropdown) so `{{baseUrl}}` etc. resolve.

2. For the calls that **auto-fill** those variables, paste a small script in the request's
   **Scripts → Post-response** tab (shown per request below). That way you never copy IDs by hand.

---

## 2. Two ways to test

- **Quick (no upload):** every call works against a **built-in sample project** if you just
  leave the `project` field out / blank. Fastest way to hit all endpoints. Start at step B2.
- **Real:** upload a video (B1), wait for `done`, then the following calls use your `projectId`.

---

## 3. The requests (in order)

> For POST-with-JSON: **Body → raw → JSON**. For upload: **Body → binary**.

### A. Sanity
- **Health** — `GET {{baseUrl}}/api/health`
- **Caption styles** — `GET {{baseUrl}}/api/caption-styles`  *(fills the style picker)*

### B1. Upload a video *(real mode)* — `POST {{baseUrl}}/api/upload`
- **Params (Query):** `name=test.mp4`, `clean_audio=true`, `remove_silences=true`, `color_grade=true`
- **Body → binary:** pick a short `.mp4`
- **Scripts → Post-response:**
  ```js
  const d = pm.response.json();
  pm.environment.set("projectId", d.project_id);
  pm.environment.set("jobId", d.job_id);
  ```

### B2. Watch progress (SSE) — `GET {{baseUrl}}/api/progress/{{jobId}}`
- This is a **stream** (Server-Sent Events). In recent Postman it shows live messages; wait until
  you see `"event":"done"`. If your Postman doesn't stream, use curl instead:
  ```bash
  curl -N http://localhost:8000/api/progress/<jobId>
  ```
- *(Quick mode: skip B1/B2 — the sample project is already prepared.)*

### B3. Words for the editor — `GET {{baseUrl}}/api/project?project={{projectId}}`
- Quick mode: drop `?project=…` to get the sample's words.

### B4. Plan B-roll — `POST {{baseUrl}}/api/broll/plan`
- **Body (JSON):**
  ```json
  { "project": "{{projectId}}", "edits": { "editedText": {} } }
  ```
  *(Quick mode: use `"project": ""` — it falls back to the sample.)*

### B5. Plan zooms — `POST {{baseUrl}}/api/zoom/plan`
- **Body (JSON):** same as B4.

### B6. Find stock clips — `POST {{baseUrl}}/api/broll/fetch`
- **Body (JSON):**
  ```json
  { "query": "city skyline at night", "spanMs": 4000, "orientation": "portrait", "page": 1 }
  ```
- **Scripts → Post-response** (grab one clip to cache next):
  ```js
  pm.environment.set("mediaUrl", pm.response.json().candidates[0].mediaUrl);
  ```

### B7. Save (cache) the clip — `POST {{baseUrl}}/api/broll/cache`
- **Body (JSON):**
  ```json
  { "url": "{{mediaUrl}}", "kind": "video" }
  ```

### B8. Make the reel — `POST {{baseUrl}}/api/broll/render`
- Simplest test = **captions-only** (no B-roll/zoom needed):
  ```json
  { "project": "{{projectId}}",
    "accepted": [], "zooms": [],
    "captionSettings": { "styleId": "amber", "bottomPercent": 22 },
    "edits": { "editedText": {} },
    "captionsOff": false, "engine": "ffmpeg" }
  ```
- **Scripts → Post-response:**
  ```js
  const d = pm.response.json();
  pm.environment.set("outputName", d.output_name);
  pm.environment.set("jobId", d.job_id);
  ```
- Then watch B2 again (`{{jobId}}`) until `done`.

### B9. Play / download the reel — `GET {{baseUrl}}/api/result/{{outputName}}`
- Click **Send and Download** to save/preview the mp4.

### B10. Reel details — `GET {{baseUrl}}/api/output-meta/{{outputName}}`
- Returns real `width, height, durationMs, size, …`.

---

## 4. The remaining routes (desktop / utility)

| Request | Method + URL | Body |
|---|---|---|
| Base video | `GET {{baseUrl}}/api/video?project={{projectId}}` | — |
| Raw transcript | `GET {{baseUrl}}/api/transcript?project={{projectId}}` | — |
| Library (list reels) | `GET {{baseUrl}}/api/library` | — |
| Delete a reel | `DELETE {{baseUrl}}/api/library/{{outputName}}` | — |
| Cached media | `GET {{baseUrl}}/api/broll/media/<key>` | — (key from the cache response) |
| Upload own B-roll | `POST {{baseUrl}}/api/broll/upload?name=clip.mp4&kind=video` | Body → binary (a clip) |
| Settings: keys present | `GET {{baseUrl}}/api/settings/keys` | — |
| Settings: defaults | `GET {{baseUrl}}/api/settings/defaults` | — |
| Settings: save defaults | `PUT {{baseUrl}}/api/settings/defaults` | JSON, e.g. `{ "bottomPercent": 20 }` |
| Settings: output dir | `GET {{baseUrl}}/api/settings/output-dir` | — |
| Publish kit (title/tags) | `POST {{baseUrl}}/api/publish-kit` | — (no body) |
| Recaption (desktop) | `POST {{baseUrl}}/api/recaption` | `{ "project":"{{projectId}}", "captionSettings":{"bottomPercent":22}, "edits":{"editedText":{}} }` |
| Regenerate (desktop) | `POST {{baseUrl}}/api/regenerate` | `{ "project":"{{projectId}}", "recipe":{"cleanAudio":true,"removeSilences":true,"colorGrade":true} }` |

---

## 5. Gotchas

- **Order matters** in real mode: upload → wait for `done` → then plan/render use `{{projectId}}`.
  In quick mode, leave `project` blank and everything runs on the built-in sample.
- **`Content-Type: application/json`** is set automatically when you choose Body → raw → JSON.
- **`/api/progress` and long renders are streams/slow** — don't expect an instant single reply;
  wait for the `done` (or `error`) event.
- **Provider calls** (`broll/fetch`, `broll/plan`, `zoom/plan`, `publish-kit`) need the API keys
  loaded (they are, if you started the backend with `. ../../vex/.env`). A `502` means the
  provider failed or a key is missing.
- **One worker only** — don't run uvicorn with `--workers 2+` (state is in memory).
