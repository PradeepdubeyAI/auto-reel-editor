# Reel Studio — Documentation

| Doc | For | Read it to understand… |
|---|---|---|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | everyone | the system, repo layout, the pipeline, how to run it, where to change things |
| [CONCURRENCY.md](./CONCURRENCY.md) | backend + devops | how many users are handled at once, the single-worker rule, env knobs, the 3.9 gotchas |
| [API.md](./API.md) | frontend + integrators | every endpoint, request/response payloads, the SSE protocol, error codes |

**Fast facts:** React + TypeScript frontend · FastAPI (Python 3.9) backend on **one uvicorn
worker** · heavy editing in a spawned pipeline subprocess (ffmpeg/Pillow + ElevenLabs/OpenAI/
Pexels). Per-user isolation via a `project_id`; a job queue caps concurrent renders. No auth,
billing, or share — out of scope. App opens on the upload screen.
