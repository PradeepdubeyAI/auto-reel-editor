# Reel Studio — AI Mobile Video Editing System
## Complete architecture & engineering report

An AI editing system that turns a raw phone recording of a talking-head into a post-ready
vertical reel — transcribed, silence-trimmed, retake-cut, colour-graded, captioned, with B-roll,
zooms, graphic cards, sound effects and music — from a single tap. In internal production use.

**Scale:** ~112,000 lines across four subsystems. 38 HTTP endpoints. 37 engine tools.
11 external services. Output spec: 1080×1920, −14 LUFS, −1.5 dBTP.

---

## 1. System shape

Three tiers on one machine, with the heavy work isolated in a subprocess:

| tier | tech | responsibility |
|---|---|---|
| **Frontend** | React + TypeScript, Vite | Mobile-first SPA. Talks only to `/api/*`. ~4,000 lines in `src/mobile/` |
| **Backend** | FastAPI + uvicorn, **single worker** | Routing, job orchestration, per-project state (in memory), SSE progress. 13 modules |
| **Pipeline** | spawned subprocess, separate venv | All editing. ffmpeg + Pillow + LLM calls. `reel-studio/pipeline.py` |
| **Engine** | `vex/` — 244 files, ~100k lines | Reusable editing library: 37 tools, agent loop, ffmpeg ops, colour science |

The subprocess boundary is deliberate: the pipeline needs a different Python and heavy native
deps (ffmpeg, Pillow, OpenCV), and a crash in a render must not take the API down.

**External services:** ElevenLabs Scribe (STT), Sarvam (Indic STT), Whisper (fallback),
OpenAI (`gpt-4o`, `gpt-4o-mini`), Gemini, Pexels + Pixabay (stock B-roll), Backblaze B2 (media
cache), DeepFilterNet (denoise), Remotion (alternate render path), ffmpeg.

---

## 2. End-to-end trace: one upload

```
POST /api/upload  (server.py)
  └─ writes payload JSON, spawns pipeline_runner.py in the VEX venv
       └─ reel-studio/pipeline.py :: run_pipeline()
            1  load + probe clip
            2  TRIM SILENCES        vex.engine.trim_silence → extract_segments_single_pass
                                    (revert-guard: if a threshold collapses the clip, retry
                                     softer; never ship a clip trimmed to nothing)
            3  CLEAN AUDIO          DeepFilterNet if present, else ffmpeg chain
            4  TRANSCRIBE           ElevenLabs Scribe (word-level), language=hin
            5  ROMANIZE             gpt-4o-mini → Hinglish in Latin script
               ── checkpoint: pre_retakes.mp4 + pre_retakes.segments.json ──
            6  RETAKE DETECTION     gpt-4o detector → 3-vote judge panel → word-exact cuts
                                    writes retake_cuts.json (the ledger) + retake_seams.json
            7  HOOK TRIM            optional: start on the strongest opening
               ── checkpoint: pregrade ──
            8  COLOUR GRADE         vex.color_grading, 8 looks × intensity
            9  UPSCALE              1080×1920
           10  CAPTIONS             ASS/libass, word-by-word, Hindi-aware paging
           11  PRO EXPORT           loudnorm −14 LUFS / −1.5 dBTP, faststart, QC
  └─ SSE progress streamed back to the client throughout (28 stage markers)

then, on demand and independently:
POST /api/broll/plan   gpt-4o-mini picks cutaway moments + graphic cards
POST /api/zoom/plan    gpt-4o-mini spans ∪ audio_peaks() → merge_signals()
POST /api/broll/render  the compositor (below) bakes everything in ONE ffmpeg pass
```

Every stage is individually toggleable, and **each destructive stage leaves a checkpoint** so the
user can undo an AI decision without re-running the pipeline.

---

## 3. The AI decision layer

Nine distinct model call sites, each scoped to the cheapest model that measurably works.

| purpose | model | design note |
|---|---|---|
| Transcription | ElevenLabs Scribe | word-level timestamps; best code-switching of the three engines tested |
| Hinglish romanisation | `gpt-4o-mini` | Devanagari → Latin, per-segment |
| **Retake detection** | **`gpt-4o`** | two-stage detector → judge; model pinned separately (below) |
| B-roll moment planning | `gpt-4o-mini` | returns spans + queries + graphic-card content |
| Zoom planning | `gpt-4o-mini` | LLM spans **unioned with** audio-energy peaks, then de-conflicted |
| Conversational editing | `gpt-4o-mini` | natural language → a **fixed tool set**, never free-form code |
| Publish kit | `gpt-4o-mini` | title, description, hashtags from the transcript |
| Colour grade selection | rule + evaluator | `color_grading_evaluator.py` scores looks per scene |
| Agent loop | `vex/agent.py` | 37 tools, tracing, fast-action path |

### Prompt-engineering patterns

**Two-stage detect-then-judge.** A deliberately loose detector proposes candidates ("it's fine to
be wrong here"); a strict judge decides. This decouples recall from precision so each can be tuned
independently.

**Self-consistency voting.** The judge runs 3× in parallel at temperature 0.7 and takes a majority.
Temperature is *raised* on purpose — three deterministic calls would just be the same answer three
times.

**Fail-closed defaults.** Any parse error, API failure or timeout in the judge returns *reject*.
An unverified claim never cuts. Rate-limit retry with exponential backoff was added after
discovering that a 429 silently read as "reject" and disabled detection.

**Model scoping.** `_RETAKE_MODEL` deliberately does **not** read the shared `OPENAI_MODEL` env
var, so pinning the hard task to `gpt-4o` doesn't make romanisation, zoom planning and chat more
expensive as a side effect.

**Positional context.** The judge's own reject categories — callback, list item, bookend,
setup/payoff — are all *positional*. It originally received two bare strings and could not see
position, which is why it confirmed 16 of 16 candidates on real footage. It now receives ±5
numbered lines with the candidate marked in place.

### The evaluation programme

Two labelled eval sets live in `reel-studio/eval/`, with a harness that reports two numbers and
**never averages them**:

- **FALSE CUTS** — must-keep lines that got cut. The hard constraint.
- **RECALL** — must-cut lines caught. The soft constraint.

| set | scripts | lines | truth-cuts | languages |
|---|---|---|---|---|
| `testset.py` | 12 | 76 | 17 | English, Hindi, Hinglish |
| `testset_v1.py` | 10 | 249 | 49 | + pure Devanagari, 15 retake types, 15 decoy classes |

Decoys are placed deliberately adjacent to real retakes: rhetorical repetition, emphasis doubling,
shared-stem lists, callbacks, refrains, intro/outro bookends, contrast pairs, question-answer pairs,
quotations, keyword echo, teaching recap, and **bilingual gloss** (a line said in Hindi then English
for the audience — structurally near-identical to a verbatim retake, but intentional).

**Measured results** on the 249-line external set:

| config | false cuts | recall |
|---|---|---|
| detector alone | 5 | 0.90 |
| **+ deterministic contiguity filter** | **3** | **0.88** |
| `gpt-4o-mini` instead of `gpt-4o` | 6 | 0.76 |

Variants tested and **rejected**, with measured reasons:

| variant | result |
|---|---|
| intent-only prompt, no category lists | recall **0.00** — returned zero groups on all 12 scripts |
| same + one line about line distance | 5 false cuts — one sentence flipped it from inert to most aggressive |
| externally-authored disfluency-theory spec | **0.17** on real footage; its evidence floor required gaps ≥0.7s, and 0.0% of post-silence-trim lines clear that |
| adjacency stated as a *prompt rule* | recall 0.82 → **0.53**, same false cuts |

That last row is the most useful finding in the programme: given line numbers **and** an explicit
instruction to reject non-adjacent pairs, the model still confirmed a bookend pair 8 lines apart.
**The constraint had to move from the prompt into code** — a 4-line contiguity filter achieved what
five prompt revisions could not.

---

## 4. Media correctness engineering

This is the deepest work in the system. Video editing has failure modes that are invisible in every
obvious check — durations look right, `ffprobe` reports sane values, each piece is individually
correct — and only appear as a *relationship* between two clocks.

### AAC concat padding accumulation

**Symptom:** fragments of removed takes audible at cut seams; captions drifting.

**Root cause:** the assembly path encoded each kept segment to its own AAC file and joined with
`-f concat -c copy`. Every AAC piece carries encoder padding, and stream-copy joining keeps all of
it — ~13 ms per join. Over ~100 silence-trim joins that is **1.32 s** of accumulated padding. The
audio stream then decoded to 103.147 s while the container declared 101.827 s, so audio slid
progressively out of step with container time while transcript timestamps stayed in decoded-audio
time. Cuts computed from word times hit the wrong audio.

**Diagnosis:** speech cross-correlation was unreliable (lossy re-encode, plus speech gives confident
peaks at wrong offsets). Switching to a **white-noise test source** — which autocorrelates to a
single sharp spike — made per-piece placement error exact and unarguable.

**Fix:** three things, each verified independently — one decode pass (kills per-piece padding),
range edges snapped to real frame presentation times, and **one `concat` filter per stream** rather
than an interleaved `concat=v=1:a=1` (a joint concat imposes the video's frame-rounding on the audio,
and the rounding accumulates).

| | before | after |
|---|---|---|
| placement error, 100-piece join | **5,921 ms** | **0.02 ms** |
| placement error, 400-piece join | — | **0.00 ms** |
| A/V stream gap | 15.6 ms | **0.0 ms** |
| real footage, 3 s → 99 s | up to 800 ms drift | **0 ms at every sample point** |

Verified across 48 kHz mono, 44.1 kHz stereo, Opus, 25 fps, 59.94 fps and no-audio inputs.

### Retake seam residue

Word-bounded cuts left the removed take's first syllable audible — "so it's not la— so it's not
lying on purpose". The fix places each cut edge inside measured silence rather than on the ASR word
boundary:

- **speech-relative gate** (p75 − 17 dB). A noise-floor anchor was tried first and failed: these
  clips are already silence-trimmed, so low percentiles sit in splice gaps, not room tone (p5 read
  −58 dB on a file whose actual room tone was ≈ −50 dB).
- **100 ms quiet run**, selected by sweeping 100/120/160/200 ms × 6 threshold estimators over 41
  real cuts. 200 ms — the value phonetics suggests, since a stop closure alone is 95–140 ms —
  measured **worse than not trimming at all** (24/41 cuts with an audible fragment vs 20/41),
  because it vetoed 71% of flanks.
- **per-side veto**: two thirds of flanks with no usable silence have one on the other side.
- a **band-limited 1.5–6 kHz** detector was implemented and removed: at a clear vowel onset that
  band read −60.8 dB, *quieter* than the pause 200 ms earlier.

Result: audible fragments **27/52 cuts → 3/52**, with **0 structural violations** — a cut can never
overlap a kept word, by construction rather than by tuning.

### Frame handling on variable-frame-rate phone footage

Phone video is genuinely VFR (one clip reported a 31 fps container against a 30.75 fps measured
average), so "one frame" is not a fixed step and must be read from the stream. Snapping range edges
to real frame presentation times makes each piece's video duration exactly equal to its audio
duration, so both streams accumulate identically.

Frame times are read from **packet** timestamps, not frame timestamps: `frame=best_effort_timestamp_time`
decodes the entire video (**29.0 s** on a 249 s clip, so minutes on a 30-minute one — enough to blow
a timeout and silently disable snapping on exactly the long videos that need it). Packet PTS need no
decode and are the same values — verified identical, 7,503 timestamps, max difference **0.0 ms**, in
**0.13 s**.

### Audio mix

**Music.** Two normalisers were silently undoing the user's setting: `amix` defaults to
`normalize=1` (divides every input by the input count), and `dynaudnorm` then lifted the quiet mix
back up — with no notion of which stream is the bed, so it swelled the music into every gap between
lines. Replaced with `normalize=0` plus a true limiter; program loudness left to the single
`loudnorm` pass. Music-to-voice separation in speech gaps: **5.5 dB → 12.1 dB**.

**Sound effects.** The bundled catalogue was never loudness-matched — measured RMS spanned
−13.7 dB to −32.6 dB, a **19 dB** spread, so one gain setting meant five different things and half
the library was inaudible under speech. Fixed by normalising each sound's *attack* to a common peak
plus a base boost. RMS normalisation was tried first and measured **worse** (whole-file RMS is not
comparable when durations run 0.10 s to 4.14 s). Then, because a short transient cannot be made
louder in a mix by turning it up — the limiter eats the gain — each hit is **compressed before its
gain**, raising density rather than height. Clearly-audible sounds: **6/17 → 14/17**, median lift
5.1 dB → 10.3 dB, zero regressions.

### The compositor

One ffmpeg pass, `render_broll_ffmpeg.py`:

- **zoompan** with per-frame `z`/`x`/`y` expressions. `crop` cannot be used — it evaluates w/h once
  at init, so a time-varying crop freezes at the t=0 value.
- **face-anchored zoom** — Haar cascade over sampled frames, outlier rejection, anchor clamped to a
  safe interior zone so a face near the edge can't drag the crop into black.
- **punch overshoot** — 12% of travel then settle, ported from a motion-recipe library whose own 8%
  attempt was imperceptible. Applied to the travel, not the scale, and deliberately *not* to slow
  pushes, which are meant to drift rather than land.
- **seam masking** — an instant framing change on each retake cut. A word-bounded cut removes the
  words but not the gesture: stroke onset leads its phrase by 200–500 ms, so the speaker is already
  moving for a sentence that no longer exists. Measured on a real seam, **no better cut point
  existed within ±300 ms**, so the movement cannot be removed and is masked instead. Two scales
  alternate, because what masks a cut is the framing *changing* across it, not the zoom being on.
- **graphic cards** — Pillow-drawn, 3 templates × 3 palettes, alpha `.mov` overlaid like B-roll.
- **captions** — ASS/libass, word-by-word, with Hindi-aware paging: trailing particles
  (`hoon`, `hai`, `mein`, `ko`…) are pulled back onto the previous page so units like
  "bataata hoon" stay together, and single-word orphan pages are merged.
- **splice fades** — 8 ms declick at every piece boundary.

A Remotion (React video) path exists and is deliberately **not** used on mobile: no headless
Chrome, no licence, ~5–9× faster.

---

## 5. Architectural patterns

| pattern | where | problem it solves |
|---|---|---|
| **Checkpoint-and-restore** | `pre_retakes.mp4`, pregrade, `retake_cuts.json` ledger | undo an AI decision without re-running the pipeline |
| **Two-stage detect-then-judge** | retake detection | decouples recall from precision |
| **Self-consistency voting** | 3-vote judge panel | one call's phrasing shouldn't decide a destructive edit |
| **Deterministic guard over LLM output** | contiguity filter, `_starts_sentence`, span-fill | the model provably ignores constraints it is merely *told*; code doesn't negotiate |
| **Single-source-of-truth registry** | `caption_styles.py`, `sfx_catalog.py`, `music_catalog.py` | one registry drives both the ffmpeg render and the React preview |
| **Client-side approximation twin** | `CaptionSample`, `CardSample`, `cssFilterFor`, CSS zoom preview | instant preview without a server round-trip; each labelled where it's exact vs approximate |
| **Operation-log replay undo** | `vex/tools/undo.py` | rebuild the timeline from the op log minus the undone step |
| **Snapshot undo** | Trim sheet | state is small; a snapshot beats hand-writing six inverse operations |
| **Subprocess isolation** | `pipeline_runner.py` | different venv, heavy native deps, crash containment |
| **Fail-closed defaults** | judge, `_openai_json` | an unverified claim never cuts |
| **Revert guard** | `trim_silence` | if a threshold collapses the clip, retry softer; never ship nothing |

---

## 6. Human-in-the-loop design

The system makes destructive AI decisions, so every one is reviewable:

- **Trim sheet** — the pre-cut video with cut regions drawn on a real audio waveform
  (`decodeAudioData`, downsampled to bars). Playback skips cut regions on a `requestAnimationFrame`
  loop with 150 ms lookahead — `timeupdate` fires ~4×/sec, which let ~250 ms of a cut region play,
  and 250 ms is a whole short word.
- **Transcript-based cutting** — tap a line to cut it, or expand it and tap word chips for an exact
  span. This exists because one second of the timeline bar is ~1.35 pt (~0.2 mm) at fit zoom, far
  below the ~5 mm floor where a touch target stops trading size for error. The bar is for
  orientation; aiming happens in the transcript.
- **Undo/redo** with server-ledger reconciliation — matching by **range**, not id, because
  re-marking a range necessarily mints a new id.
- **Shared `EditSurface`** — one player + waveform + timeline + add-at-playhead, used by Zooms,
  Cards, SFX and B-roll, so there is one interaction to learn instead of five.
- **Two-tier honesty** — every approximate preview says so in the UI rather than letting the user
  discover it.

---

## 7. Cost model

Measured from the real call structure and current prices, per video:

| source length | cost | dominant driver |
|---|---|---|
| 1 min | **$0.014** | retake detection (29%) |
| 2 min | $0.020 | — |
| 5 min | $0.044 | transcription |
| 10 min | **$0.088** | transcription (42%) |
| 60 min | $0.497 | transcription (44%) |

Under ~2 minutes, retake detection dominates because it is `gpt-4o` over the whole transcript
regardless of length. From ~5 minutes up, transcription dominates — it is the only component that
scales with raw audio and cannot be shortened. The judge panel is ~33% of a long video's spend.

**Re-renders cost no API budget** — the transcript and all plans are reused. That is a direct
consequence of the checkpoint architecture.

---

## 8. Honest limitations

- **Retake recall is ~0.88 on labelled sets, ~0.67 on real footage.** Deliberately biased against
  false cuts. Roughly 1 in 8 retakes survives.
- **Within-line stumbles cannot be fixed by line removal.** "It's an— it's MCP." has the stumble
  and the correction in one transcript line; that needs word-level cutting.
- **Both eval sets are now burned as instruments** — the prompt and filters have been iterated
  against them, so further numbers from either are in-sample. Held-out real footage is the missing
  measurement.
- **All Hinglish results are from synthetic text.** No real Hinglish upload has been labelled.
- **Romanisation runs before detection**, so non-deterministic spelling between takes could break
  exact-match rules on Hindi content specifically. Untested.
- **Single-worker backend, in-memory state.** Correct for the current deployment; not horizontally
  scalable as-is.
- **Three short SFX remain quiet** — a limiter ceiling on transients, not a bug that gain fixes.
- **The contiguity filter is measured but not yet shipped** into `pipeline.py`.
