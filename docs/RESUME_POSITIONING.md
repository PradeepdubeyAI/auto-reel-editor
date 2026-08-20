# Resume positioning — Reel Studio (AI mobile video editing system)

For **Senior AI Engineer / AI Architect / Staff ML Engineer / AI Platform Lead** roles.
Every number below is measured and traceable to code, docs or eval output. Nothing is invented.

---

## 0. The single most important thing this project gives you

2026 hiring research is blunt about one thing:

> *"If your AI resume doesn't mention evaluation, hiring managers assume you've shipped unevaluated
> features. In 2026 that's a disqualifier at serious companies."*
> — [AI Developer Hiring 2026](https://www.digitalapplied.com/blog/ai-developer-hiring-skills-that-matter-2026)

**You have a real evaluation programme**: two hand-labelled eval sets (325 lines, 66 ground-truth
labels, 15 retake types, 15 adversarial decoy classes), a harness that reports false-positive rate
and recall **separately and never averaged**, and a documented record of prompt variants tested and
**rejected with measured reasons**.

That is rarer than any model or framework name, and it is the thing to lead with. Most candidates
claim they "used GPT-4". You can show you *measured* it and chose against the cheaper model on
evidence (6 false cuts vs 3).

The second-rarest asset: you have a documented case where **an LLM provably would not honour a
constraint stated in the prompt, and you moved it into code** — with the numbers for both. That is a
senior-level judgement most people never articulate.

---

## 1. Positioning — same project, four framings

| target role | one-line framing |
|---|---|
| **Senior AI Engineer** | "Designed and built a production LLM pipeline that automates video editing end-to-end — nine model call sites, a two-stage detector/judge with self-consistency voting, and a labelled eval harness that gates every prompt change." |
| **AI Architect** | "Architected a 112k-line, four-tier AI media system: React client, FastAPI orchestration, subprocess-isolated pipeline, and a 37-tool engine — with checkpoint-and-restore so every destructive AI decision is reversible." |
| **Staff ML Engineer** | "Owned the full lifecycle of an applied-LLM system: dataset construction, adversarial eval design, model selection on measured precision/recall trade-offs, cost engineering, and failure analysis down to the signal-processing layer." |
| **AI Platform Lead** | "Built the AI editing platform used internally at a media company — a one-tap pipeline plus nine human-in-the-loop review surfaces, at $0.014–$0.088 per video in inference cost." |

---

## 2. The project entry — three lengths

Format follows the **Google XYZ formula** — *Accomplished [X] as measured by [Y] by doing [Z]* —
which is the [most ATS-friendly and most concise](https://atsverification.com/blog/star-vs-xyz-resume-bullets/)
of the common structures. Keep STAR for the interview, not the page.

### 2-line version (when space is tight)

> **Reel Studio — AI Video Editing System** · *Architect & sole engineer · in internal production use*
> Four-tier LLM media pipeline (112k LOC, 38 APIs, 37-tool engine) that auto-edits talking-head video
> to a post-ready vertical reel. Built a 325-line adversarial eval harness that gates prompt changes;
> reduced audio-placement error from 5,921 ms to 0.02 ms by diagnosing accumulated codec padding via
> white-noise cross-correlation. Inference cost $0.014–$0.088/video.

### 4-bullet version (recommended default)

> **Reel Studio — AI Mobile Video Editing System** · *Architect & sole engineer*
> *Python · FastAPI · React/TS · OpenAI · ffmpeg · in internal production use at a media company*
>
> - **Architected a four-tier AI media pipeline** (React client → FastAPI orchestration →
>   subprocess-isolated pipeline → 37-tool editing engine; 112k LOC, 38 endpoints, 11 external
>   services) that converts a raw phone recording into a 1080×1920 / −14 LUFS reel in one tap.
> - **Designed a two-stage LLM detector→judge with 3-vote self-consistency** for destructive edit
>   decisions, and built a 325-line hand-labelled eval harness (15 adversarial decoy classes across
>   English/Hindi/Hinglish) that reports false-positive rate and recall separately — reaching **0.88
>   recall at 3 false positives in 249 lines**, and selecting `gpt-4o` over `gpt-4o-mini` on measured
>   precision (3 vs 6 false cuts) rather than assumption.
> - **Proved a prompt-stated constraint was unenforceable and replaced it with a deterministic
>   guard**: instructing the model to reject non-adjacent candidates dropped recall 0.82→0.53 while
>   fixing nothing; a 4-line structural filter took false positives to **zero** at no API cost.
> - **Diagnosed and eliminated a class of silent audio/transcript desynchronisation** caused by
>   accumulated AAC encoder padding (~13 ms per concat join, 1.32 s over 100 joins) — isolated it
>   with white-noise cross-correlation after speech correlation proved unreliable, cutting placement
>   error from **5,921 ms to 0.02 ms** across 400-piece joins and six codec/frame-rate configurations.

### 6-bullet version (for an AI-architect application or a portfolio page)

Add these two:

> - **Engineered the inference cost model** — scoped the expensive model to the one task that
>   measurably needed it so the pin could not leak into romanisation, planning and chat; profiled
>   per-stage cost to **$0.014 (1 min) – $0.088 (10 min) per video**, and made all re-renders
>   API-free by reusing checkpointed transcripts and plans.
> - **Built nine human-in-the-loop review surfaces** over the AI's destructive decisions —
>   checkpoint-and-restore, a per-decision ledger with reasons, snapshot undo/redo with server
>   reconciliation, and transcript-level word-range cutting designed around measured touch-precision
>   limits (a 1-second timeline target is ~0.2 mm at fit zoom, far below the ~5 mm error floor).

---

## 3. Bullet bank — by competency

Pick to match the job description. **★ = strongest.**

### LLM systems design
- ★ Designed a two-stage detect→judge architecture that decouples recall from precision, with the
  detector deliberately biased loose and the judge fail-closed on any parse or API error.
- ★ Implemented 3-vote self-consistency at raised temperature — three deterministic calls would
  return the same answer three times and buy no reliability.
- Scoped nine distinct LLM call sites to the cheapest model that measurably worked per task.
- Built a natural-language editing agent that maps free-form requests onto a **fixed, audited tool
  set**, never generated code, with a user-confirmation gate before any change applies.
- Fused LLM output with a deterministic signal (audio-energy peaks) and de-conflicted by priority,
  spacing and coverage budget rather than trusting either source alone.

### Evaluation rigour
- ★ Constructed two hand-labelled eval sets (325 lines, 66 labels) spanning English, Hindi,
  romanised Hinglish and pure Devanagari, with 15 deliberately adversarial decoy classes placed
  next to true positives.
- ★ Enforced an asymmetric-cost metric — false positives and recall reported separately, never
  averaged — and rejected two higher-recall prompt variants because they cut real content.
- ★ Identified and corrected a **confound in my own instrument**: a per-language result was actually
  measuring retake-type placement, because type and language were not crossed in the fixture.
- Established sampling-noise bounds (±1 false positive, ±0.06 recall) and discarded improvements
  inside them as unproven.
- Traced an anomalous "0.000 recall" result to a thread-pool race in the harness mutating a shared
  env var, invalidating every variant scored during that window.

### Architecture
- ★ Four-tier system with a deliberate subprocess boundary isolating the heavy pipeline (separate
  interpreter, native deps, crash containment) from the API.
- ★ Checkpoint-and-restore at every destructive stage, making all AI decisions reversible without
  re-running the pipeline — and making re-renders free of inference cost.
- Single-source-of-truth registries so one definition drives both the server-side render and the
  client preview, eliminating drift by construction.
- Operation-log replay undo in the engine; snapshot undo in the UI — chosen per layer by state size.

### Media / systems depth
- ★ Root-caused accumulated codec padding across concatenation, verified with a white-noise source
  because speech cross-correlation gave confident false peaks.
- Established that phone footage is genuinely variable-frame-rate and snapped all edit boundaries to
  real frame presentation times so audio and video accumulate identically.
- Cut a per-render frame scan from **29.0 s to 0.13 s (223×)** by reading packet timestamps instead
  of decoding every frame — verified identical across 7,503 timestamps, max difference 0.0 ms.
- Diagnosed two competing loudness normalisers silently undoing a user setting; improved
  music-to-voice separation in speech gaps from **5.5 dB to 12.1 dB**.
- Loudness-matched an unmatched sound library (19 dB RMS spread) and raised clearly-audible assets
  from **6/17 to 14/17** by compressing transients before gain rather than after.

### Product / HCI judgement
- Grounded interaction design in measured constraints rather than preference — replaced a
  sub-millimetre timeline target with transcript word-chips after computing the effective target size.
- Labelled every client-side preview as exact or approximate in the UI instead of letting users
  discover the difference.

---

## 4. Metrics table — everything genuinely quotable

| metric | value | what it proves |
|---|---|---|
| System size | ~112,000 LOC, 4 subsystems | architectural scope |
| API surface | 38 endpoints | breadth of the platform |
| Engine tools | 37 | agentic tool design |
| External services integrated | 11 | integration complexity |
| LLM call sites | 9, individually model-scoped | cost engineering |
| Eval sets | 2 sets, 325 lines, 66 labels, 4 languages | evaluation rigour |
| Adversarial decoy classes | 15 | eval design maturity |
| Retake detection | **0.88 recall, 3 FP / 249 lines** | measured model performance |
| Model selection evidence | `gpt-4o` 3 FP vs `mini` 6 FP | decision on data |
| Prompt-vs-code finding | 0.82→0.53 recall (prompt) vs 0 FP (code) | LLM-limits judgement |
| Audio placement error | **5,921 ms → 0.02 ms** | debugging depth |
| A/V stream gap | 15.6 ms → 0.0 ms | correctness |
| Drift on real footage | up to 800 ms → **0 ms** | end-to-end verification |
| Audible cut artefacts | 27/52 → 3/52 cuts | quality improvement |
| Frame scan | 29.0 s → 0.13 s (**223×**) | performance work |
| Music separation | 5.5 dB → 12.1 dB | signal-processing competence |
| SFX audibility | 6/17 → 14/17 assets | systematic fix over one-off |
| Inference cost | **$0.014–$0.088 / video** | cost ownership |
| Output spec | 1080×1920, −14 LUFS, −1.5 dBTP | broadcast-standard delivery |

Research note: with no revenue or user-count numbers available, the accepted substitutes are
**scale, scope and efficiency** metrics — data processed, components integrated, before/after
performance ([Resume Worded](https://resumeworded.com/how-to-quantify-resume-key-advice),
[Glassdoor engineering thread](https://www.glassdoor.com/Community/technology/how-do-you-add-metrics-and-quantify-your-resume-when-you-are-a-software-engineer-who-dont-really-get-such-numbers-or-percentages)).
Every row above is one of those three.

---

## 5. Interview deep-dives — five likely questions

**"Walk me through how you'd evaluate an LLM feature where a wrong answer is destructive."**
Asymmetric cost: deleting content a creator wanted is unrecoverable, leaving a stumble in is an
annoyance. So the metric is two numbers never averaged, the judge is fail-closed on any error, and a
variant with better recall and one extra false positive loses. Then the concrete story: two variants
at 0.86 recall rejected because they cut real content on refrain and bookend decoys.

**"When would you not use an LLM?"**
The strongest story you have. Adjacency — a retake is a *consecutive* attempt — is trivially
checkable. Stated in the prompt with line numbers visible, the model still confirmed a bookend pair
8 lines apart and recall fell 0.82→0.53. Four lines of code took false positives to zero at no API
cost. Rules that are structurally checkable belong in code; the model is for judgement that isn't.

**"Tell me about the hardest bug you've debugged."**
The codec padding one. Every obvious check passed — durations correct, each piece individually
accurate — because the fault was a *relationship* between container time and decoded-audio time.
Include the methodological turn: speech cross-correlation gave confident wrong answers, so switching
to a white-noise source made placement error exact. That instrument choice is the interesting part.

**"How did you control cost?"**
Per-stage profiling, model scoping so an expensive pin can't leak into cheap tasks, and an
architecture where re-renders reuse checkpointed transcripts and plans so iteration is API-free.
Then the honest nuance: the judge panel is ~33% of a long video's spend and confirmed 16/16 on real
footage — I know it's insurance rather than an active filter, and I can defend keeping it or cutting it.

**"What's still wrong with it?"**
Recall is ~0.67 on real footage vs 0.88 on labelled sets. Both eval sets are now burned as
instruments because I've iterated against them, so held-out real footage is the missing measurement.
Within-line stumbles need word-level cutting. All Hinglish results are synthetic. — *Answering this
well is worth more than any bullet above; it shows you know where your evidence stops.*

---

## 6. Skills / keyword mapping

Phrased the way 2026 postings phrase it. Only list what the project genuinely evidences.

**LLM & GenAI** — LLM application architecture · prompt engineering · multi-stage LLM pipelines ·
self-consistency / ensemble voting · tool-calling agents · structured output (JSON mode) ·
LLM evaluation harnesses · adversarial eval-set design · guardrails & fail-closed design ·
inference cost optimisation · model selection & benchmarking

**Backend & platform** — Python · FastAPI · async job orchestration · SSE streaming · subprocess
isolation · state checkpointing · REST API design (38 endpoints) · concurrency design

**Media / ML systems** — speech-to-text integration (ElevenLabs, Whisper, Sarvam) · forced-alignment
timing · ffmpeg filtergraph engineering · audio DSP (loudness normalisation, sidechain compression,
limiting) · OpenCV (Haar cascade) · signal cross-correlation for verification · EBU R128 / −14 LUFS
delivery

**Frontend** — React · TypeScript · real-time media UI · Web Audio API · human-in-the-loop review UX

**Practice** — architecture ownership · measurement-driven iteration · failure analysis ·
technical documentation · multilingual/code-switched NLP (Hindi/Hinglish)

Note from the research: [agentic work — tool calling, multi-step reasoning, MCP, orchestration — is
described as the senior AI engineer's strongest 2026 differentiator](https://www.digitalapplied.com/blog/ai-developer-hiring-skills-that-matter-2026).
Your 37-tool engine and tool-calling chat agent are directly on that axis; lead with them over
framework names.

---

## 7. What NOT to claim

Being caught overstating costs more than the bullet gained. This project does **not** evidence:

- **Distributed training, fine-tuning, LoRA/QLoRA, RLHF** — no model training of any kind.
- **Production scale or SLOs** — single-worker backend, in-memory state, no load figures, no uptime
  or p95 latency data. Do not imply horizontal scale.
- **Multi-tenancy** — per-project isolation exists in-process; that is not multi-tenant infrastructure.
- **Team leadership or mentoring** — solo build. Claim *architecture* ownership, not people ownership.
- **User/business metrics** — no DAU, retention, revenue or time-saved study. "In internal
  production use" is the honest ceiling.
- **RAG or vector search** — not in this system. Do not add it for keywords.
- **MLOps tooling** (MLflow, Kubeflow, feature stores) — not used.

Two numbers to be ready to contextualise rather than hide: **recall 0.67 on real footage** (the
0.88 is on labelled sets), and **~1 in 8 retakes still missed** — deliberately, because the cost of
a false cut is far higher.

---

## 8. The README a reviewer will actually click

Published projects with a repo or demo are [substantially easier for reviewers to verify](https://resumeoptimizerpro.com/blog/how-to-list-projects-on-resume),
so the README is part of the application. Order it for a 90-second skim:

1. **One sentence + one output video.** Show the result before any architecture.
2. **The architecture diagram** — you already have one in `docs/ARCHITECTURE.md`.
3. **The measured results table** — section 4 above, verbatim. This is the differentiator; put it
   above the tech stack, not below.
4. **The evaluation section** — the eval sets, the two-number metric, the rejected variants with
   their measured reasons. Nobody else's README has this.
5. **Two engineering deep-dives** — the codec padding diagnosis and the prompt-vs-code finding.
   Full detail, with numbers. These are what a senior reviewer reads properly.
6. **Honest limitations** — section 8 of `PROJECT_ARCHITECTURE.md`. It raises credibility, not lowers it.
7. Tech stack and setup last.

Keep `docs/ARCHITECTURE.md`, `docs/API.md` and `docs/CONCURRENCY.md` — existing internal docs are
themselves evidence of architectural practice.

---

## Sources

- [AI Developer Hiring 2026: Skills That Actually Matter](https://www.digitalapplied.com/blog/ai-developer-hiring-skills-that-matter-2026)
- [STAR vs XYZ vs PAR Resume Bullets (2026)](https://atsverification.com/blog/star-vs-xyz-resume-bullets/)
- [Google XYZ Resume Method (2026 guide)](https://stylingcv.com/blog/google-xyz-resume-method-how-to-write-bullet-points-that-get-you-hired-2026-guide/)
- [AI Engineer Resume Keywords (2026): 60+ Skills for the GenAI Era](https://www.resumeadapter.com/blog/ai-engineer-resume-keywords)
- [How to Quantify Your Resume — Resume Worded](https://resumeworded.com/how-to-quantify-resume-key-advice)
- [How to List Projects on a Resume](https://resumeoptimizerpro.com/blog/how-to-list-projects-on-resume)
- [Quantifying engineering work without PM-style metrics (Glassdoor)](https://www.glassdoor.com/Community/technology/how-do-you-add-metrics-and-quantify-your-resume-when-you-are-a-software-engineer-who-dont-really-get-such-numbers-or-percentages)
