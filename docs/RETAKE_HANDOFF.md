# Retake Removal — state of play, measurements, and open questions

Handoff for further research. Everything below is measured on real footage, not estimated.
Where I got something wrong, it says so.

## 1. System as it stands

**Pipeline order (this matters — see §5):**
```
upload -> colour grade -> clean audio -> TRANSCRIBE (ElevenLabs Scribe, language=hin)
       -> romanize (gpt-4o-mini) -> TRIM SILENCE -> checkpoint (pre_retakes.mp4)
       -> RETAKE DETECT + JUDGE -> cut -> B-roll / zoom / captions / render
```
Retake detection runs **after** silence trimming, on the trimmed transcript.

**Models:** detector and judge both `gpt-4o`, pinned via `_RETAKE_MODEL`, deliberately NOT
reading `OPENAI_MODEL` (shared with romanize/zoom/chat, which are fine on mini).
Measured: `gpt-4o-mini` scored **recall 0.000** on the eval set — caught zero of 18 retakes.
Total failure, not marginal.

**Structure:** loose detector (temp 0.2, one call) -> 3-vote judge panel (temp 0.7, majority).

## 2. Current prompts

### Stage 1 — detector
```
You are proposing CANDIDATE retakes in a single continuous recording -- places where the speaker may have stopped and redone a line. This is only a first pass; a separate, stricter check decides what actually gets cut, so propose anything that looks plausible -- it's fine to be wrong here, don't try to be certain.

Below is the full numbered, timestamped transcript. Group line numbers that look like different attempts at the same point into a retake group. For each group, pick which line number to KEEP -- normally the last attempt, unless an earlier one clearly reads as the intended final take.

Return ONLY JSON: {"groups": [{"keep": <line number>, "cut": [<line numbers>], "reason": "..."}]}. If nothing looks like a retake, return {"groups": []}.
```

### Stage 2 — judge (run 3x, majority vote)
```
You are deciding whether to CUT a line from someone's video because it's a discarded retake -- an earlier, abandoned attempt at what a later KEEP line says.

Cutting content the creator wanted is a serious mistake; leaving a retake in is a minor inconvenience. So when the evidence is genuinely balanced, REJECT.

But apply that caution to real ambiguity, not to clear-cut cases. CONFIRM when any of these holds:
  - The CUT line is a verbatim or near-verbatim repeat of the KEEP line. A repeat carries no information the KEEP line doesn't already carry, so nothing is lost by removing it.
  - The CUT line is an incomplete fragment of what the KEEP line says in full (including a line broken off mid-word or mid-clause).
  - The CUT line contains an explicit self-interruption ('wait', 'sorry', 'scratch that', 'let me start over', 'hang on') and the KEEP line covers the same ground. The speaker has stated they are discarding it; take them at their word even if the wording differs.
  - Both lines state the same fact, value or claim, and the KEEP line supersedes it -- including when they differ ONLY in a specific number, name or quantity. That difference is the correction itself, not a reason to keep both.

REJECT when the CUT line does real work the KEEP line doesn't. Name which applies:
  - it makes a genuinely different point, or is about a different subject
  - it is deliberate repetition for rhetorical effect or emphasis
  - it is a callback to, or a deliberate reprise of, something said earlier
  - it is one entry in a list or sequence where each entry is distinct
  - it is one half of a deliberate pair (question then answer, contrast, setup then payoff)
  - it is quoting or attributing something to someone else

Return ONLY JSON: {"confirmed": true|false, "reason": "..."}
```

## 3. Eval set result (synthetic, 10 topics)

10 scripts / 76 lines / 18 genuine retakes / 58 must-keep, with deliberate decoys:
rhetorical repetition, shared-stem lists, callbacks, refrains, bookends, contrast pairs,
quotations, question-then-answer.

- **precision 1.000, recall ~0.76, zero false cuts across 5 full runs**
- Two prompt variants reached recall 0.86 but cut real content on the refrain and bookend
  decoys, so they were rejected. Never cutting real content was the hard constraint.

## 4. Real-footage test (the important part)

Two real videos, current pipeline:

| video | lines | detector groups | judge confirmed | lines cut |
|---|---|---|---|---|
| video_20260716_211022 (4.3 min, English) | 45 | 7 | **7/7** | 12 |
| video_20260718_182908 (6.3 min) | 61 | 9 | **9/9** | 31 |

**The judge confirmed 16 of 16.** It is rubber-stamping on real footage — every miss is a
detector miss. The 3-vote panel costs 3x gpt-4o calls per group (~33% of a long video's API
spend) and rejected nothing here. It did reject decoys on the synthetic eval, so it is
insurance, not an active filter.

### Hand-labelled ground truth, video_20260716_211022

TRUTH-CUT = should be removed (my labels). current = caught by the shipped pipeline.
spec = caught by the candidate spec in §5.

```
[01]   12.30-  14.72 gap= 0.00s  AI will give you completely wrong answer                       
[02]   17.48-  20.18 gap= 2.76s  and sound hundred percent sure about it.                       
[03]   26.65-  28.10 gap= 6.47s  This is called hallucination.                                  
[04]   35.69-  37.94 gap= 7.59s  It's not a bug you will see an error                           
[05]   38.02-  38.30 gap= 0.08s  for.                                                           
[06]   42.32-  42.94 gap= 4.02s  It just,                                                       TRUTH-CUT
[07]   45.44-  47.28 gap= 2.50s  it just makes things up.                                       
[08]   50.94-  53.13 gap= 3.66s  Fake quotes, fake statistics,                                  
[09]   55.84-  58.10 gap= 2.71s  fake research that don't exist.                                
[10]   63.44-  65.72 gap= 5.34s  All delivered in same tone.                                    TRUTH-CUT
[11]   70.52-  73.24 gap= 4.80s  All delivered in same confidence.                              TRUTH-CUT
[12]   80.34-  83.30 gap= 7.10s  All delivered in same confident tone.                          
[13]   87.58-  89.80 gap= 4.28s  As a fact that actually true.                                  TRUTH-CUT current✓
[14]   92.64-  94.86 gap= 2.84s  As a fact that's actually true.                                
[15]   99.92- 100.20 gap= 5.06s  Why?                                                           
[16]  108.06- 110.62 gap= 7.86s  Because AI isn't looking things up.                            
[17]  116.06- 117.14 gap= 5.44s  It's predicting.                                               TRUTH-CUT current✓
[18]  120.42- 121.52 gap= 3.28s  It's predicting.                                               TRUTH-CUT current✓
[19]  123.84- 124.96 gap= 2.32s  It's predicting.                                               
[20]  130.66- 133.02 gap= 5.70s  It's predicting what a good answer...                          TRUTH-CUT current✓
[21]  135.76- 138.86 gap= 2.74s  It's predicting what a good answer sounds like                 
[22]  141.12- 142.20 gap= 2.26s  word by word.                                                  
[23]  148.14- 149.84 gap= 5.94s  So it's not lying on purpose.                                  TRUTH-CUT current✓
[24]  153.70- 155.60 gap= 3.86s  So it's not lying on purpose.                                  
[25]  160.14- 161.58 gap= 4.54s  It genuinely doesn't know                                      
[26]  164.38- 167.19 gap= 2.80s  the difference between true and sounds true.                   
[27]  171.76- 174.92 gap= 4.57s  Which means the smartest way to use AI                         
[28]  177.40- 178.70 gap= 2.48s  isn't to trust blindly.                                        TRUTH-CUT current✓
[29]  181.68- 184.88 gap= 2.98s  Isn't to trust it blindly.                                     
[30]  189.38- 190.70 gap= 4.50s  It's to treat it...                                            TRUTH-CUT
[31]  194.52- 197.24 gap= 3.82s  It's to treat it like a smart intern.                          
[32]  200.16- 200.60 gap= 2.92s  Fast,                                                          TRUTH-CUT
[33]  201.20- 201.78 gap= 0.60s  useful.                                                        TRUTH-CUT current✓
[34]  203.98- 205.50 gap= 2.20s  Fast, useful.                                                  TRUTH-CUT current✓
[35]  209.76- 211.24 gap= 4.26s  Fast, useful.                                                  
[36]  214.78- 216.96 gap= 3.54s  But you still check the important stuff.                       
[37]  222.18- 223.02 gap= 5.22s  Come and check.                                                TRUTH-CUT
[38]  224.96- 226.72 gap= 1.94s  Come and check.                                                
[39]  229.92- 231.10 gap= 3.20s  And I'll send you--                                            TRUTH-CUT current✓ spec✓
[40]  233.22- 234.96 gap= 2.12s  And I will send you three.                                     TRUTH-CUT current✓
[41]  238.12- 240.20 gap= 3.16s  And I'll send you three.                                       TRUTH-CUT current✓ spec✓
[42]  243.14- 244.46 gap= 2.94s  And I'll send you th--                                         TRUTH-CUT current✓ spec✓
[43]  246.38- 248.38 gap= 1.92s  And I'll send you three things.                                
[44]  250.70- 252.20 gap= 2.32s  I always verify                                                
[45]  255.02- 256.74 gap= 2.82s  before trusting an AI answer                                   
```

**Score: caught 12/18, recall 0.67, zero false positives.**

### The 6 misses, and they are not subtle

| missed | why it is obviously a retake |
|---|---|
| `[6] "It just,"` | abandoned fragment of `[7] "it just makes things up."` |
| `[10] "All delivered in same tone."` + `[11] "...same confidence."` | two earlier attempts at `[12] "...same confident tone."` |
| `[30] "It's to treat it..."` | fragment of `[31] "It's to treat it like a smart intern."` |
| `[32] "Fast,"` | fragment of the 33-35 cluster it did catch |
| `[37] "Come and check."` | **verbatim duplicate** of `[38] "Come and check."` |

They fall into three mechanical classes:
1. **verbatim adjacent duplicate** — string equality, zero LLM, zero risk
2. **trailing fragment + completion** — ends in `,` `...` `--` AND is a prefix of the next line
3. **varied-wording cluster** — same claim, three phrasings; genuinely needs judgment

Classes 1 and 2 are 4 of the 6 misses and need no model at all.

## 5. Tested a full replacement spec — it did worse

A detailed spec was proposed (gap-annotated transcript, locality rule R1, evidence floor R4,
Hindi/Hinglish markers, bilingual-gloss guard, 3 detector samples unioned at temp 0.6, single
strict judge at temp 0). I implemented its Stage 1 verbatim and scored it on the same video.

```
                    caught  missed  false positives  recall
current pipeline        12       6         0           0.67
spec stage-1             3      15         0           0.17
```

Two of three detector samples returned **zero groups**.

### Root cause: the gap signal is incompatible with our pipeline ORDER

The spec's `R4 EVIDENCE FLOOR` requires gap >= 0.7s, or a marker, or truncation.

```
RAW  n=44 median=3.24s pct>=0.7s=95.5%
POST 264ed309 n=16 median=0.22s pct>=0.7s=0.0%
POST 60632dcb n=16 median=0.22s pct>=0.7s=0.0%
POST 45718b61 n=16 median=0.22s pct>=0.7s=0.0%
POST d16e882f n=91 median=0.04s pct>=0.7s=0.0%
```

- On **raw** audio, 95.5% of lines clear 0.7s -> the signal discriminates nothing, it is
  uniformly "yes". The model saw 2-4s gaps everywhere and read them as separate deliberate
  statements.
- On **post-silence-trim** audio (where our detector actually runs), **0.0%** of lines clear
  0.7s, because silence removal already deleted the exact pauses the spec depends on. R4 would
  reject nearly everything.

Both ends fail, for opposite reasons. The spec's own top-priority fix ("#2 pause data first")
assumes retake detection happens BEFORE silence trimming. Ours happens after.

### What is right in that spec regardless

- **Locality (20s window)** — our prompt has NO locality constraint. Nothing stops it grouping
  line 12 with line 87. This has not bitten us only because recall is low. Real risk.
- **Hindi/Hinglish editing markers** — our marker list is English-only ("wait", "sorry",
  "scratch that"). Speakers say *ruko, ek minute, phir se, nahi nahi, galat bol diya*.
- **Bilingual gloss guard** — Hinglish creators say a line in Hindi then in English so both
  audiences follow. Looks like a verbatim retake, is not. We would eventually cut these.
- **Union detector samples + ONE strict judge** — well argued, and our 16/16 judge confirmation
  is direct evidence the majority-vote judge is the wrong place to spend calls.
- **Deterministic validation** — cheap, catches malformed output, no tokens.
- **Preserve the breath before the kept take** — we do not do this.

## 6. What has been tried and rejected, with reasons

| tried | result |
|---|---|
| `gpt-4o-mini` for detect/judge | **recall 0.000.** Retroactively explained why earlier prompt tuning "never stuck" |
| Two higher-recall prompt variants (0.86) | Rejected — cut real content on refrain/bookend decoys |
| Per-video prompt edits | Abandoned. Worked on one video, broke others. Replaced by the 10-script eval set |
| Spec Stage 1 as written | recall 0.67 -> 0.17 (see §5) |
| `_EDGE_GUARD = 0.15` on cut edges | Caused 8 orphan slivers + 10 mid-word edges. Removed |
| Unbounded silence expansion at cut edges | **Deleted the word "not"** from "so it's not lying on purpose", inverting meaning. Reverted immediately |
| Band-limited 1.5-6kHz silence detection | At a clear vowel onset that band read -60.8dB, QUIETER than the pause 200ms earlier. Does not discriminate. Removed |
| `_PAUSE_KEEP` 250ms -> 150ms to shrink visible leftover gesture | **No effect at all** — the giveback is clamped to the quiet run, so the knob has ~10ms of authority. Reverted and documented |
| Re-ASR as a quality oracle | Invalid. Whisper hallucinated on 99.97% of non-speech clips (Careless Whisper, FAccT 2024) |

## 7. Related fixes already shipped (context for anyone reading the cut quality)

- **AAC concat padding drift.** Joining N encoded clips with `-c copy` kept every piece's encoder
  padding: ~13ms per join, 1.32s accumulated over ~100 joins. Audio slid progressively out of step
  with container time while transcript timestamps stayed in decoded-audio time, so cuts computed
  from word times hit the wrong audio. Verified by white-noise cross-correlation: **5921ms max
  placement error -> 0.02ms**. Fixed with single-pass filtergraph + frame-snapped ranges + one
  concat per stream.
- **Caption misalignment after user trims.** The project's `video` pointer was swapped to the
  re-cut file but `transcript` still pointed at the ingest-time whisper.json, so captions kept
  pre-trim timings. Fixed.
- **Overhang trim** (stops a fragment of the removed take surviving at the seam): speech-relative
  gate (p75-17dB), 100ms quiet run, per-side veto. Audible fragments **27/52 cuts -> 3/52**,
  0 structural violations (a cut can never overlap a kept word, by construction).

## 8. Open questions for research

1. **Should retake detection move BEFORE silence trimming?** That would make gap data real and
   is the precondition for the spec's evidence floor. Cost: the transcript is then of untrimmed
   audio, and every downstream stage assumes the trimmed timeline. How much rework?
2. **Is a deterministic pre-pass the right recall fix?** Exact-duplicate + prefix-fragment
   detection would have caught 4 of the 6 misses with no model and no false-positive risk. Is
   there a principled reason not to?
3. **Is the 3-vote judge worth keeping** given 16/16 confirmation on real footage but real
   rejections on synthetic decoys? Is the synthetic set unrepresentative, or is the real footage
   just easier?
4. **Locality window** — what value? The spec says 20s, 12s for scripted. We have no data.
5. **Hinglish false-positive classes** — bilingual gloss is the predicted big one. We have no
   Hinglish eval data at all; the 10-script set is English.
6. **Recall target.** Currently ~0.67-0.76 by design (asymmetric cost). Is that the right point,
   or should missed retakes be surfaced for one-tap removal instead of cut automatically?

## 9. Caveats on my own numbers

- Ground-truth labels in §4 are **mine**, not an independent annotator's. Reasonable people could
  disagree on `[32] "Fast,"`.
- The spec test was **Stage 1 only, on raw audio, on one English video**. Enough to show R4 is
  order-dependent; NOT enough to judge the spec on Hinglish, which is what it is aimed at.
- The synthetic eval set is English-only and written by me, so it may share blind spots with the
  prompt it was used to select.
