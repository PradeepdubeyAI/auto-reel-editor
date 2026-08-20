# Reel Editor — Feature Roadmap Research

Research date: 2026-07-21. Grounded in a full read of the actual `reel-studio-ui` codebase (not assumptions), plus competitive research across CapCut, InShot, VN, Canva, Adobe Premiere Rush/Firefly, Descript, VEED, Opus.pro, Instagram, TikTok, VSCO, and others.

## How to read this doc

Each of the 6 requested features gets: what competitors actually do, the concrete recommendation, exactly where it goes in the existing UI, the technical approach grounded in this repo's real stack, and a v1/v2 phasing split. There's also a 7th section for gaps found beyond the original 6, and a final suggested build order.

---

## Cross-cutting findings (read this first — changes how you should think about the whole roadmap)

**Several of these "new features" are actually just wiring, not new engineering** — the hard part is already built and sitting unused:

- **Color grading / filters**: `vex/color_grading.py` already implements 8 real looks (natural, vibrant, cinematic, warm, cool, documentary, punchy, auto) with full numeric profiles, shot-aware per-scene grading, and a quality evaluator. Today the app hardcodes `look="natural", intensity=0.5` with zero UI. Exposing this is a picker + one API parameter, not a new grading engine.
- **B-roll upload**: `POST /api/broll/upload` already exists and works — it's just marked desktop-only and never called from the mobile frontend. The upload leg of "let users add their own B-roll" is mostly frontend wiring.
- **Chatbot**: `vex/agent.py` is a real, working conversational video-editing agent with a genuine multi-provider LLM abstraction (already supports Claude Sonnet, Gemini, OpenAI-compatible, Ollama, LM Studio). It's CLI-only today, totally disconnected from the web app. The reusable part is its provider abstraction + agent-loop pattern, not its literal tool schema (which is built around a timeline/trim/merge model the mobile app doesn't have).
- **Publish/share**: the Saved screen's Instagram/WhatsApp buttons are dead `<button>` elements with no `onClick` at all. Meanwhile `POST /api/publish-kit` already exists, already generates an LLM-drafted title/description/hashtags from the transcript, and is called from **zero** frontend code. This is your single cheapest, highest-value fix in this entire document — see Opportunities below.

**The one real structural gap underneath everything**: there is **no database anywhere**. All state is in-memory Python dicts in a single-worker FastAPI process, swept by a TTL janitor; nothing survives a backend restart. This is fine for the app as it exists today, but a music library, an SFX library, a "your past B-roll uploads," or even "remember my last-used color look" all *want* persistence and none of them get it for free. Recommendation: add **one small SQLite file** (no ORM, stdlib `sqlite3`, matches the app's existing zero-infra philosophy) scoped to metadata/reference rows only (asset paths, library entries, last-used prefs). Leave the existing in-memory `jobs` dict alone — that's legitimately ephemeral, SSE-driven state and shouldn't move. Do this **before or alongside** any feature below that implies a persistent library, not after.

---

## 1. Music — stock library (legal) + user upload

### The licensing question, answered

This was your actual uncertainty, and the research gives a clean, unambiguous answer: **never use any platform-native "free" music library** — not Instagram/Meta Sound Collection, not TikTok's library, not standard-tier YouTube Audio Library. All three are confirmed to be contractually scoped to playback *inside that platform's own app*, not to being baked into an exported MP4 a user then posts wherever they want:

- Meta's own Sound Collection terms explicitly forbid using the audio "separately from Meta Company Products" — so even Meta's own royalty-free tracks can't legally back a video that gets cross-posted to TikTok or YouTube.
- YouTube's "Standard license" tracks are restricted to YouTube videos; only the CC-BY-4.0-tagged subset is portable anywhere (with attribution).
- CapCut/InShot's bundled libraries are licensed for content *made and exported through their own app* — not for arbitrary reuse either.

Since this app renders server-side and hands the user a plain MP4 to post wherever they choose, the only model that actually works is an **independently-cleared, perpetual, worldwide, royalty-free catalog** with no platform restriction — e.g. Pixabay Music for a free MVP tier (free for commercial use, no attribution, no platform restriction), upgrading to Epidemic Sound/Soundstripe later if there's budget for a nicer catalog. Every track should show a small "Free to post anywhere" badge (Canva does per-track license-tier badges — worth copying).

**User-uploaded music**: handled exactly like every competitor handles it — no rights-checking, just a one-time consent checkbox that shifts liability to the user. This is identical to the pattern this app already uses for user-uploaded video.

### UI placement

New **Setup CATALOG toggle** ("Music", default **OFF** — unlike most toggles, since silence/original audio is a valid default many users want). Opens a bottom sheet ("Choose music") using the exact same sheet pattern as Fine-tune's existing "B-roll clips"/"Edit captions" sheets: a Library tab (search + genre/mood filter chips, reusing the app's existing unused category-accent colors — plum/indigo/forest/teal/periwinkle/amber/rust/clay/maroon, one per genre) and an Upload tab. Add "Music" as a 5th entry in Fine-tune's `OVERLAY_KEYS` so users can swap the track or re-tune ducking after seeing the render, via the existing re-render-without-re-upload flow.

### Technical approach

- `GET /api/music/library` — static catalog metadata (bundled flat files, no DB needed since it's static).
- `POST /api/music/upload` — same shape as the existing (currently unwired) `POST /api/broll/upload`.
- Ducking: expose as named presets (Off/Light/Medium/Strong), not raw numbers — implemented server-side via ffmpeg `sidechaincompress` keyed off the voice track, matching how the color-grading engine already hides numeric knobs behind named "looks."
- Slots into the same ffmpeg render pass as trim/color-grade/captions — no new job type, no new progress mechanism.

### Phasing

**v1**: bundled Pixabay-tier catalog (30-60 tracks) + user upload with consent checkbox + Setup toggle + named ducking presets + Fine-tune re-render support.
**v2+**: paid catalog upgrade (Epidemic Sound/Soundstripe) once budget exists, LLM-suggested track picks reusing the same pattern as B-roll planning, per-scene beat-aware ducking.

---

## 2. User B-roll upload + LLM auto-placement + correction

### Is "auto-decide then fix after a full render" the right UX?

**No — and the research is unusually consistent on why.** Every AI-placement competitor found (VEED, OpusClip, Captions.ai, CapCut) shows the AI's placement decision on a live, free timeline **before** any expensive export, and lets the user fix it right there — because in their architecture, preview and render are the same free surface. This app's architecture is different: a render here is a real, non-trivial server-side ffmpeg+Pillow job gated by a 2-slot semaphore. That makes "pay the render cost twice for a placement mistake" *worse* here, not less bad — which argues **for** adding a review step, not against the current post-render-fix model.

The good news: the LLM planning call (`broll_plan.py`) is pure text — a cheap completion, no render involved. So the fix is nearly free: **add a lightweight "Review placement" step between planning and render** — a card list (thumbnail + assigned moment + one-line reason, tap to reassign from a shortlist or remove) that gates the actual render kickoff. Keep the existing post-render Fine-tune sheet as a safety net for anything that still looks wrong once real footage is composited (an LLM text-plan can't anticipate framing/motion issues).

### Chatbot or structured UI for correction?

**Structured UI, not a chatbot.** This correction task is inherently spatial (which clip, which moment, how long) — every competitor with real AI-placement (OpusClip, Captions.ai, VEED) solves it with drag/tap, not typing. Building a conversational NLU loop for "move this 2 seconds earlier" is a large lift for a worse interaction than a drag handle.

**One real gap to fix regardless**: today's Fine-tune "B-roll clips" sheet is swap/remove only — there's **no retime concept at all**. OpusClip and Captions.ai both treat drag-to-retime as table stakes. Fix this independent of the new user-upload feature.

### UI placement

Setup screen: extend the "B-roll" toggle (or add "Your B-roll") to reveal a multi-select upload picker + a one-line description field per clip — feeds the already-built `POST /api/broll/upload`. New bottom sheet between Setup and Processing: "Review B-roll placement" — one card per clip, thumbnail + assigned moment + reason, Reassign/Remove. Confirming this sheet is what actually kicks off the real render. Fine-tune's existing "B-roll clips" sheet gets a simple two-handle trim/offset slider added per clip (for both stock and user clips) — the minimal structured equivalent of drag-to-retime.

### Technical approach

Extend `broll_plan.py`'s planning call to accept user-uploaded clip IDs + descriptions as an additional candidate pool. This call should stay synchronous (fast/cheap) but needs a timeout + heuristic fallback so a slow LLM call can't tie up one of only two `Semaphore(2)` slots. Extend the Fine-tune B-roll payload from `{accept, reject}` to `{accept, reject, retime: {id: {start_offset_ms, duration_ms}}}`, constrained server-side to each clip's real footage window.

### Phasing

**v1**: wire the existing upload endpoint into Setup; extend the LLM candidate pool to include user clips; add the pre-render review sheet (reassign-from-shortlist, no free-form retime yet); add retime sliders to Fine-tune's existing B-roll sheet.
**v2+**: richer review cards with actual extracted-frame thumbnails at candidate timestamps; a persistent cross-session B-roll library (blocked on the persistence work above).

---

## 3. Transitions

### What to actually build (this will feel like an under-build, and that's correct)

Every competitor list (CapCut, InShot, VN) advertises dozens of transitions, but their own "best of" round-ups converge on the same handful (cross-dissolve, whip pan, zoom, glitch, slide) — the long tail is rarely touched. More importantly, editorial best-practice research for **short-form talking-head + B-roll specifically** (not generic long-form video) is blunt: default to the hard cut, reserve transitions for a specific job, and a real structure test found a *structured* edit with no fancy transitions got 21% higher completion than a heavily-transitioned version of the same clip. Adobe's own Premiere Rush — the most "pro" tool in the comparison set — ships only 3 transitions total.

**Recommendation: build exactly ONE technique, not a library or a picker** — a short (~120-200ms) alpha crossfade at the entry and exit of every B-roll/Ken-Burns/graphic-card overlay. This isn't really a new stylistic feature; it's a correctness fix. The graphic cards already do this (a baked 0.1s fade-in/0.15s fade-out) — it just was never extended to plain B-roll video or Ken Burns image overlays, which currently cut with zero fade at all. Do **not** build a transition-type picker — that directly contradicts the "hard-cut-first, transitions must earn their place" consensus for this genre.

### Where and how (grounded in the actual render code)

B-roll/cards/Ken-Burns in this app are **overlays on a continuous, never-cut base** (`overlay=0:0:enable='between(t,a,b)'`), not separately-concatenated clips — so ffmpeg's `xfade` filter (built for crossfading between two concatenated clips) doesn't apply here. The correct primitive is an **alpha fade on the overlay layer itself**, exactly like the existing graphic-card code already does. Extend that same fade treatment to `kind=='video'` and `kind=='image'` (Ken Burns) overlays in `render_broll_ffmpeg.py`, widening the `enable='between()'` window slightly to match the fade envelope. New Setup CATALOG toggle ("Smooth cutaways", default ON, binary only — no style/duration picker). **Scope this to overlay boundaries only** — never apply it to jump cuts on the underlying talking-head footage (silence-trim cuts), where a hard cut is the *correct*, expected treatment.

### Phasing

**v1**: one technique, one toggle, no picker.
**v1.1 (only if usage data asks for it)**: a single alternate "quick zoom-punch" style reusing the existing auto-zoom machinery — evaluate against real usage, don't build speculatively.

---

## 4. Chatbot

### One chatbot, not two

The B-roll-correction chatbot question and the general post-render-edit chatbot question are **the same problem**, not two features. Both are just mutating fields on the same parameter object that already feeds `/api/broll/render` (`accepted[]`, `zooms[]`, `captionSettings`, etc). Splitting into two bots forces either a router (an extra failure mode) or forces the user to address two different bots for one sentence like "make the dog clip longer and change the captions to yellow." Build **one agent, one system prompt, one flat tool schema** targeting the shared state object.

### What's real in the market

Descript's "Underlord" is the most directly comparable shipped product — and its most valuable idea is a **verify-then-repair loop**: after executing an edit, a second LLM pass diffs the result against the request and re-invokes itself on mismatches. Adobe's new Firefly AI Assistant validates the core architecture recommended here: they didn't build a new generative engine, they wrapped their *existing* tools (Auto Tone, Generative Fill, etc.) as agent-callable functions — directly analogous to wrapping this app's existing render params as tool calls. RunwayML's Aleph (video-to-video diffusion editing) is worth explicitly ruling out — it's a fundamentally different, much more expensive architecture than this app needs for "change the caption color."

### Reuse, don't rebuild

`vex/agent.py` already proves the exact pattern works (a provider-agnostic tool-calling loop) but is never called by `server.py` today and its tool schema (`trim_clip`, `merge_clips`) doesn't match anything the mobile render pipeline actually accepts. Reuse its **infrastructure** — `vex/providers/claude_provider.py` (already talks to Claude Sonnet), the agent-loop shape, the compound-instruction-splitting heuristic, the trace/event shape (maps almost verbatim onto the app's existing SSE mechanism) — with a **brand new, narrower tool schema** scoped to what the mobile app can actually do (`retime_broll_clip`, `swap_broll_clip`, `set_caption_style`, `set_zoom_style`, and later `set_color_grade_look`, `add_music`, `add_sfx`).

### One important architectural split to respect

There are two tiers of "change" in this app, and a chatbot must know the difference:
- **Cheap/render tier** (captions, B-roll, cards, zoom) — calls the existing `/api/broll/render` with mutated params, same SSE progress stream, no new frontend work.
- **Ingest tier** (trim-silence strength, clean-audio strength, and any future color-grade look) — baked at `/api/upload`, not reachable from `/api/broll/render` at all. A chat request like "make the colors warmer" cannot be satisfied by the cheap path today.

**v1 should scope the chatbot to the cheap tier only** and have it respond "that requires reprocessing your original clip, want me to do that?" for ingest-tier asks rather than silently no-op-ing. v2 adds a `POST /api/reingest` that reuses the already-stored source file (no re-upload needed) as a second, more expensive tool the agent should confirm before invoking.

### UI placement

Fine-tune screen, as a new bottom sheet (a chat pill near the existing "Re-render" footer) — consistent with the existing sheet pattern, no new (7th) screen needed. Inside the sheet: transcript + input + a lightweight confirmation chip per tool call ("Caption color → yellow") before Re-render actually fires, so the single render slot isn't burned on a misunderstood instruction (borrowing Descript's verify pattern).

### Phasing

**v1**: unified chat scoped to render-tier params only, bottom sheet on Fine-tune.
**v2**: add `/api/reingest` for base-tier corrections; this is also the delivery vehicle for the color-grading "looks" picker, so it can ship via chat before it needs its own dedicated UI.
**v3**: music/SFX chat commands, once those features actually exist — don't let the tool schema promise things that aren't built yet.

---

## 5. Filters / color options

### Clarify the concept first

"Filters" (Instagram/TikTok/VSCO-style named visual presets) and "color grading" (this app's existing automatic corrective engine) are related but distinct in the market — but in this codebase, **they should be the same feature**, because the grading engine already has 8 well-defined looks. Every platform researched (Instagram, TikTok, CapCut, VSCO, InShot) converges on the same UX: a grid/strip of live-preview thumbnails, tap to apply, then a single intensity slider — never raw sliders as the primary interaction. VSCO is the strongest precedent for "preset + one strength slider + optional advanced layer" — manual tools refine a chosen preset, they don't replace it.

### Recommendation

Rename the "Colour grade" concept into an explicit **Look picker** exposing the 8 already-built looks (natural/vibrant/cinematic/warm/cool/documentary/punchy/auto), each with a live-preview thumbnail generated from the user's own footage (cheap here — the engine already exists server-side; no competitor bothers with personalized previews) and a single 0-100% intensity slider mapped directly to the `intensity` parameter that already exists in the engine but is hardcoded to 0.5. **Don't build a second filter system** — this is a UI + one API parameter, not new grading math.

### UI placement

Setup screen: the existing "Colour grade" CATALOG row becomes tappable, opening a bottom sheet ("Choose a look") with the 8 thumbnails in a grid (using the app's existing category-accent colors, one per look) + intensity slider. Default stays "auto" so zero-choice users see no behavior change. Fine-tune screen: add "colorGrade" as a 5th `OVERLAY_KEYS` entry so the look is re-tunable after render without re-upload.

### Technical approach

Thread a `look` string (already validated via `normalize_color_grade_look`) and `intensity` float through the render request in place of the hardcoded values — touches the call site and request schema, not the grading math. For preview thumbnails: reuse `render_color_grade_preview_frames()`, which already exists internally for candidate scoring — expose it as a lightweight preview endpoint, one frame × 8 looks, cached per-project (not regenerated on every sheet-open, to avoid contending with the `Semaphore(2)` render gate).

### Phasing

**v1**: 8-look picker at Setup, wired to the real (currently hardcoded) parameter.
**v1.5**: add to Fine-tune's `OVERLAY_KEYS`.
**v2**: universal intensity slider (already-designed for this) + 2-3 advanced manual sliders (brightness/contrast/saturation) layered on top of the chosen look, VSCO-style — deferred since no competitor treats manual sliders as more than a secondary refinement.

**Watch out for**: "auto" runs shot-aware grading that varies per scene — a single static preview thumbnail could misrepresent it; label it distinctly ("Auto — adapts per scene") rather than as a fixed look like the other 7.

---

## 6. Sound effects

### Market check

CapCut/InShot/VN all implement SFX as a categorized library with tap-to-preview + tap-to-place-at-playhead — that part is table stakes. But **nobody in the mainstream short-form tooling landscape has shipped a true automatic "SFX fires itself on every cut" feature** (confirmed across CapCut, InShot, VN, VEED, Wisecut, Opus Clip) — even CapCut's beat-detection and SFX-placement are two separate manual tools the user chains together. This is a genuine market gap, not a catch-up feature, **if** it's built with real restraint.

### Build as two layers on the same asset library

**(a) Auto SFX-on-cut** — new Setup toggle, default ON. Don't build new cut/beat detection — reuse `zoom_plan.py`'s existing audio-peak + LLM-emphasis-moment detection (the same signal that already drives auto-zoom and B-roll placement). Hard-cap total hits at 3-5 per video regardless of length, protect the hook and close, map moment type to sound category (hard cut → whoosh, stat reveal → pop/ding, punchline → hit-strong) rather than always the same file.

**(b) Manual SFX library** — new Fine-tune bottom sheet (categorized grid, tap-to-preview via a plain `<audio>` element, "place at current playhead position" using the same scrub position the video player already tracks). Show small tick marks on the existing scrub bar for placed hits (auto + manual) so density is visible at a glance.

### Real asset-library note

This project already has a working seed: `edit/sfx_assets/` (ding/pop/whoosh-deep/hit-strong) plus the ffmpeg amix/adelay/dynaudnorm mixing pattern proven in `edit/build_aitool_audio.py` this session. There are also **13 additional MIT-licensed presets** already vendored (unused) in the pycaps venv (click, glitch, heart-beat, swoosh, and others) — promote all 17 into a first-class `videos/sfx_assets/` directory with a static JSON manifest before building new detection logic. Do a quick licensing-provenance sanity check on these before treating them as production-ready — MIT covers the *code* they came bundled with, not automatically the audio files' own clearance.

### Phasing

**v1**: ship the manual picker first (pure UI + the already-proven mixing code, lowest risk) using the expanded 17-sound catalog.
**v1.5**: auto SFX-on-cut, reusing `zoom_plan.py`'s detection, hardcoded to the sparse 3-5-hit default.
**v2+**: user-adjustable density, retime for auto-placed hits, licensed asset-pack expansion.

**Watch out for**: collisions between an auto-placed hit and a zoom-punch or caption-pop landing on the same instant — three simultaneous "stingers" at one frame is a real failure mode that would violate the sparse/breathing taste this feature is trying to protect.

---

## 7. Beyond your list of 6 — what the research found

### Do this first — it's nearly free

The **Saved screen's share buttons are dead code**. `POST /api/publish-kit` already exists, already calls an LLM to generate a title/description/hashtags from the transcript, and is called from **zero** frontend code. Fix: on entering the Saved screen, call `/api/publish-kit`, show the generated caption/hashtags in a copyable block, wire the WhatsApp/Instagram buttons to the Web Share API (`navigator.share`) with the existing download link kept as fallback. **No new backend work at all** — pure frontend wiring of two things that already exist. Ship this before anything else in this document.

### Confirmed real gaps worth building

- **Speed ramping** — absent (the only `setpts` usage today is internal B-roll timing math, not a user feature). Cheap CATALOG toggle, simple relative to what's already shipped.
- **Multi-aspect-ratio export** (9:16 / 1:1 / 16:9) — absent (the only "9:16" reference anywhere in the frontend is a CSS style, not a real export option). Real, standard expectation. **Caveat**: caption/B-roll-card positioning is safe-zone-tuned for 9:16 specifically — ship 9:16 + 1:1 crop first (low risk, the existing caption safe-zone mostly survives a center-square crop); defer full 16:9 until there's an explicit overlay-reflow pass.
- **Auto hook-line + thumbnail picker** (Opus.pro's pattern) — absent, but cheap: reuses the exact same LLM-over-transcript pattern already proven in `broll_plan.py`/`publish_kit.py`, plus existing ffmpeg frame extraction. Fits neatly as a new Fine-tune sheet.
- **Templates/presets** ("Talking-head hook," "Podcast clip," etc.) — absent; cheapest possible version is hardcoded bundles of existing CATALOG toggle values + a look + a caption style. Doesn't need the persistence layer.
- **Text-to-speech voiceover** — absent, real competitor differentiator (CapCut, Descript Overdub), but a heavier lift (new provider integration + ducking logic). Recommend as v2/v3, not urgent.

### Confirmed as already a relative strength (not a gap)

Caption style variety — `caption_styles.py` already has a real registry of multiple named, fully-specified styles, materially richer than what InShot/VN expose. No action needed here beyond, eventually, unlocking a couple of styles currently parked as unavailable.

### Researched and explicitly rejected (with reasons, don't build these without a real trigger)

- **Green-screen/background removal** — needs a segmentation ML model, a capability class the ffmpeg+Pillow stack doesn't have. Heaviest lift on this list.
- **Multi-clip stitching** — the entire pipeline (silence-trim, transcript-driven captions, auto-zoom) assumes one contiguous source; this would need an ingestion redesign for a workflow that isn't this app's genre. Single-clip-in is a reasonable v1 scope, not a real limitation.
- **Trending-audio suggestions** — needs a live trend-data feed/licensing relationship, a data-partnership problem, not an engineering gap.
- **Analytics/performance tracking post-publish** — wrong tool category (needs OAuth into each platform's API); even CapCut/InShot don't build this in-app.
- **Batch/multi-video processing** — would directly stress the one binding architectural constraint (`Semaphore(2)` on a single worker, no real job queue). Revisit only after a genuine multi-worker backend exists.
- **Full undo/version history** — partially already covered: Fine-tune's re-render-over-same-base already functions as a lightweight redo loop.

---

## Suggested overall build order

1. **Publish/share fix** (Opportunities §1) — nearly free, ship immediately.
2. **Persistence layer** (one SQLite file, metadata only) — unblocks everything below that implies a library.
3. **Filters/Look picker** (§5) — biggest ratio of user-visible value to engineering effort; the hard part is already built.
4. **Transitions** (§3) — small, contained, mostly a correctness fix to existing code.
5. **B-roll upload + review step + retime** (§2) — meaningful lift, but the upload endpoint already exists.
6. **SFX** (§6) — manual picker first (v1), auto-on-cut as a fast-follow.
7. **Music** (§1) — needs a real licensing decision made (this doc gives you the answer) and a curated catalog sourced before it can ship.
8. **Chatbot** (§4) — the biggest lift; sequence last since it benefits from color-grade-look, music, and SFX all already existing as tools it can call.
9. **Speed ramp, multi-aspect export, hook/thumbnail, templates** (Opportunities) — interleave as cheap wins alongside whichever numbered feature you're building at the time.
