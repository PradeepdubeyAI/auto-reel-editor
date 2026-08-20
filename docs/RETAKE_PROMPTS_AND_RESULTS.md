# Retake prompts, every variant tested, and the measured result

Complete record. Includes the variants that failed and the two claims of mine that did not
survive re-measurement.

---

## The test setup

**Eval set** — `reel-studio/eval/testset.py`, in the repo (the previous one was in /tmp and was
lost). 12 scripts, 76 lines, **17 must-cut / 59 must-keep**. Languages: 33 lines English,
23 Hinglish, 20 Hindi. Decoys placed deliberately next to real retakes: rhetorical repetition,
emphasis doubling, shared-stem list, callback, refrain, **bookend (intro/outro catchphrase)**,
contrast pair, question-then-answer, quotation, keyword echo, teaching recap, **bilingual gloss**.
One control script where nothing should be cut.

**Harness** — `reel-studio/eval/harness.py`. Reports two numbers, never averaged:
- **FALSE CUTS** — must-keep lines that got cut. The expensive failure.
- **RECALL** — must-cut lines caught. The cheap failure.

Model and prompt passed explicitly, never via env mid-run. An earlier harness monkeypatched a
shared env var from a thread pool and silently scored variants on the wrong model.

**Also tested against real footage** — `video_20260716_211022` (4.3 min, 45 lines), hand-labelled:
18 must-cut. All 18 sit in 11 contiguous runs ending at their keeper; not one non-adjacent retake.

---

## 1. Detector prompts

### A — currently shipped

```
You are proposing CANDIDATE retakes in a single continuous recording -- places where the speaker may have stopped and redone a line. This is only a first pass; a separate, stricter check decides what actually gets cut, so propose anything that looks plausible -- it's fine to be wrong here, don't try to be certain.

Below is the full numbered, timestamped transcript. Group line numbers that look like different attempts at the same point into a retake group. For each group, pick which line number to KEEP -- normally the last attempt, unless an earlier one clearly reads as the intended final take.

Return ONLY JSON: {"groups": [{"keep": <line number>, "cut": [<line numbers>], "reason": "..."}]}. If nothing looks like a retake, return {"groups": []}.
```

| model | runs | false cuts | recall |
|---|---|---|---|
| gpt-4o | 1 | 3 | 0.82 |
| gpt-4o | 3 unioned | 3 | 0.88 |
| gpt-4o-mini | 1 | 4 | 0.76 |
| gpt-4o-mini | 3 unioned | 6 | 0.76 |

Per language (gpt-4o x3): **en recall 0.86 / 3 false cuts · hi 0.75 / 0 · hinglish 1.00 / 0**

On real footage: recall 0.667 (12/18), 0 false cuts, 7 detector groups -> judge confirmed 7/7.

### B — pure intent, no category lists, no marker lists

```
A single speaker recorded this in one take. Some lines are attempts they abandoned and
immediately restarted; the rest is content they meant to say.

For any line that echoes a nearby line, ask ONE question: what job does this line do in the
finished video?

If you can name a job it does -- it emphasises something, it is an item in a list, it translates
the point for part of the audience, it calls back to something earlier, it quotes someone, it
sets up what comes next, it opens or closes the video -- then the speaker meant it. Keep it.

If the only thing it does is be an attempt that did not land -- broken off, restarted, corrected
a moment later -- then they did not mean it to be in the video. That is a retake.

Two constraints that follow from what a retake IS:
- A retake and its replacement are CONSECUTIVE attempts at the same moment. Lines far apart in
  the video are not retakes of each other however similar the words, because the speaker moved on
  and came back on purpose.
- The speaker must be restarting the same sentence, not merely returning to the same subject.

You are reading ASR output. It may mix languages inside one sentence and its romanisation is
inconsistent, so judge by meaning and sound, never by exact spelling. A word that means 'wait'
or 'again' or 'sorry' in any language the speaker uses is the speaker telling you they are
discarding the attempt -- take them at their word.

Cutting something the creator wanted is far worse than leaving a stumble in, so when it is
genuinely unclear, keep it.

Return ONLY JSON: {"groups": [{"keep": ..., "cut": [...], "job": "...", "reason": "..."}]}
```

**FALSE CUTS 0, recall 0.00.** Returned zero groups on every one of 12 scripts. The caution
clause with no concrete positive trigger makes the model refuse entirely.

### C — B plus one line: "line distance IS time distance -- use it"

**FALSE CUTS 5, recall 0.94.** One added sentence flipped it from inert to the most aggressive
variant tested. That is how unstable intent-only framing is without anchors.

### D — the v2 spec's detector (disfluency framing, gap= annotations, evidence floor R4)

**recall 0.17** on real footage, vs 0.667 for A. Two of three samples returned zero groups.
Root cause measured: R4 requires gap >= 0.7s, but

| transcript | median gap | lines clearing 0.7s |
|---|---|---|
| raw audio | 3.24s | **95.5%** |
| post-silence-trim (where detection actually runs) | 0.22s | **0.0%** |

Uniformly true on raw (discriminates nothing), uniformly false post-trim (rejects everything).
The gap direction was also backwards: measured, retake gaps are **shorter** (2.89s median) than
between-beat gaps (3.54s), not longer.

---

## 2. Judge prompts

### System prompt (unchanged throughout)

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

### What changed today: the USER message

**Before** — two bare strings, no position:
```
KEEP: "But you still check the important stuff."
CUT: "Check the important stuff."
```

**After** — ±5 numbered lines, candidate marked in place:
```
Transcript has 12 lines. Showing 1-12.

CUT? [001] One connector that links any AI to any app.
     [002] Before MCP, every connection was hand-built.
     ...
KEEP [009] One connector that links any AI to any app.

Decide: are line(s) [1] discarded attempts at what line 9 says?
```

**Why.** Six of the judge's own REJECT categories -- callback, list entry, half of a pair,
rhetorical reprise, quotation, different subject -- are POSITIONAL. It was asked to detect
"is this a callback?" while unable to see whether anything sat between the two lines. That is
the real explanation for the 16/16 confirmation on real footage, which I first misread as
leniency. It was not lenient; it had nothing to reject with.

### Judge variants, end to end (detector A unchanged)

| judge input | false cuts | recall |
|---|---|---|
| blind (two bare strings) | 3 | 0.76 |
| + context (±5 numbered lines) | 3-4 | 0.76-0.82 |
| + context + adjacency rule in the system prompt | 4 | **0.53** |

Context did not reduce the count -- it moved which errors occur. On the bookend script, blind cut
`[1]` and missed the real retake `[7]`; with context it found `[7]` and cut `[9]` instead.

**The adjacency rule in the prompt failed outright.** Recall 0.82 -> 0.53, same false cuts, and
the bookend false cut survived *with the line numbers visible and an explicit instruction to
reject non-adjacent pairs*. Conclusion: adjacency must be enforced in code, not asked of the model.
Not applied.

---

## 3. The three false cuts, identical on both models across every run

Not sampling noise -- deterministic:

| script | cut | what it is |
|---|---|---|
| en-mcp-bookend | `[1]` | intro catchphrase, matched to the outro 8 lines later |
| en-intern | `[7]` "Check the important stuff." | rhetorical echo after a completed sentence |
| en-correction | `[4]` "Ten thousand." | emphasis echo |

All three are decidable only from position. All three are the class the contiguity constraint
removes structurally.

---

## 4. Deterministic rules, zero model calls (from the v2 spec, implemented and measured)

Exact-duplicate + truncated-prefix + interpolation, on real footage:

| | recall | false cuts |
|---|---|---|
| spec predicted | 0.722 | 0 |
| **measured** | **0.667** | **2** |

The false cuts are `[19]` and `[38]` -- and both are **keepers**. The spec argued its truncation
guard spares `[19]`, and verified that in isolation; but `[18]` and `[20]` both get cut, so the
**interpolation rule absorbs `[19]` anyway**. Same for `[38]`, which is the keeper of run `{37}`.
So interpolation as written deletes keepers, the exact failure the design exists to prevent.
Guarding it (never interpolate a grammatically complete line) costs `[41]` and lands at 0.611 --
below the shipped pipeline.

Verified separately: **contiguity holds on all 11 runs**, every keeper survives, zero exceptions.

---

## 5. Two claims of mine that did not survive re-measurement

**"gpt-4o-mini: recall 0.000, the task is beyond this model."** Wrong. Raw responses show mini
returns valid JSON with real groups on every script; `response_format: json_object` makes fenced-
markdown parse failure impossible. Re-measured: **mini recall 0.76, 6 false cuts** vs
**4o recall 0.88, 3 false cuts**. Keeping the 4o pin is still right -- but because mini deletes
twice as much real content, not because it cannot do the task. The 0.000 almost certainly came
from the harness race I found and fixed and then never re-ran mini against.

**"precision 1.000, zero false cuts across 5 runs."** True on the old eval set. The new set --
with a bookend-at-distance decoy and Hinglish -- breaks the shipped prompt **3 times**. The old
set was too easy and I quoted its numbers as a guarantee.

---

## 6. Methodology note

Between two runs of the same config, "context only" gave 3 false cuts / 0.76 recall and then
4 / 0.82. **±1 false cut and ±0.06 recall is sampling noise.** Differences that small -- including
several I quoted earlier -- are not reliable. The adjacency recall drop (0.82 -> 0.53) is far
outside that band and is real.

---

## 7. Where this leaves it

Nothing tested beats the shipped prompt on both axes. Best candidates:

1. **Contiguity enforced in CODE** before the judge is called. Predicted to remove the bookend
   false cut at zero recall cost, since all 18 real retakes are contiguous runs. Test was running
   when this was written.
2. **Keep the context-aware judge** -- no aggregate gain yet, but it is strictly more informed and
   it found a true positive the blind judge missed.
3. **Two-tier output** -- auto-apply high confidence, surface the rest for one-tap removal. The
   review UI now exists, so a missed retake becomes a suggestion rather than staying in the video.
   This is what makes a recall/precision trade unnecessary.

Untested and unknown: whether any of this holds on Hinglish real footage. Every real-footage
number here is from one English video.
