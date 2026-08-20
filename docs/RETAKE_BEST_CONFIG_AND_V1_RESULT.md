# Best config + result on retake-eval-v1 (249 lines, 49 truth-cuts)

Handoff. The eval set is externally authored, not by me -- which matters, because my own set was
too easy and gave me two numbers that did not survive contact with this one.

## The config

**Model** `gpt-4o` (pinned via `RETAKE_MODEL`, deliberately NOT reading `OPENAI_MODEL`, which is
shared with romanize/zoom/chat). Detector temperature 0.2, one call, full transcript in one shot.
**Plus a deterministic contiguity filter in code** (below) applied to the detector's output.

There is no second LLM stage in the measured config -- the judge is bypassed, because on real
footage it confirmed 16/16 and every false cut it made was decidable only from position it could
not see.

## The prompt (unchanged from what ships today -- no variant beat it)

```
You are proposing CANDIDATE retakes in a single continuous recording -- places where the speaker may have stopped and redone a line. This is only a first pass; a separate, stricter check decides what actually gets cut, so propose anything that looks plausible -- it's fine to be wrong here, don't try to be certain.

Below is the full numbered, timestamped transcript. Group line numbers that look like different attempts at the same point into a retake group. For each group, pick which line number to KEEP -- normally the last attempt, unless an earlier one clearly reads as the intended final take.

Return ONLY JSON: {"groups": [{"keep": <line number>, "cut": [<line numbers>], "reason": "..."}]}. If nothing looks like a retake, return {"groups": []}.
```

User message is the numbered transcript, nothing else:
```
[001] Aaj main aapko banake dikhaungi Indori poha.
[002] Ingredients bahut simple hain.
...
```

## The filter (~4 lines, no API cost)

```python
def contiguous(keep, cuts):
    cs = sorted(cuts)
    return bool(cs) and cs[-1] == keep - 1 and cs == list(range(cs[0], cs[-1] + 1))
```
Any proposed group failing this is dropped before it can cut. This exists because **stating
adjacency in the prompt does not work** -- given the line numbers AND an explicit instruction to
reject non-adjacent pairs, the model still confirmed a bookend pair 8 lines apart, and recall fell
0.82 -> 0.53. Code does not negotiate.

## Result

```
DETECTOR ALONE           FALSE CUTS 5   recall 0.90  (44/49)
DETECTOR + CODE FILTER   FALSE CUTS 3   recall 0.88  (43/49)
```

| script | language | lines | truth | caught | missed | FALSE CUTS |
|---|---|---|---|---|---|---|
| S01 | hinglish | 25 | 5 | 4 | `[11]` | `[]` |
| S02 | hinglish | 26 | 6 | 6 | `[]` | `[]` |
| S03 | hindi_heavy_hinglish | 24 | 5 | 4 | `[13]` | `[]` |
| S04 | english | 25 | 3 | 4 | `[]` | `[18]` |
| S05 | hinglish | 24 | 5 | 5 | `[7, 8]` | `[17, 23]` |
| S06 | english | 25 | 5 | 5 | `[]` | `[]` |
| S07 | hinglish | 24 | 4 | 2 | `[10, 18]` | `[]` |
| S08 | hindi_devanagari | 25 | 5 | 5 | `[]` | `[]` |
| S09 | hinglish | 24 | 6 | 6 | `[]` | `[]` |
| S10 | english | 27 | 5 | 5 | `[]` | `[]` |

Per language, with filter:

| language | recall | false cuts |
|---|---|---|
| english | 1.00 | 1 |
| hindi_devanagari | **1.00** | 0 |
| hindi_heavy_hinglish | 0.80 | 0 |
| hinglish | **0.81** | **2** |

## Which of the set's traps landed

**S07 `r2` earlier_take_kept -- THE FILTER'S ONE REAL FAILURE.** The detector correctly proposed
`keep=9, cut=[10]`: the speaker starts to redo line 9, abandons the redo, moves on. The filter
rejected it because the cut sits AFTER the keeper. The model got this right and my code threw it
away. The set's own note predicted exactly this: *"every 'keep the last attempt' rule fails here."*
**Fix: allow a cut immediately after the keeper as well as before.** A 26-line-away bookend is
still rejected either direction, so this should be free.

**S10 -- the filter earned its place.** It dropped `keep=27, cut=[1]` (refrain bookend, 26 lines
apart) and `keep=3, cut=[4]`. Both would have been false cuts.

Net on this set: filter is **-2 false cuts, -1 true positive**.

**S08 pure Devanagari -- recall 1.00, 0 false cuts.** No normaliser damage, because matching is
done by the model, not by a regex. The set's warning about `[^a-z0-9\s]` applies to deterministic
L1-style rules, not to this path.

**S02 inverted gap profile -- recall 1.00.** Nothing is hard-coded about gap sign, because the
prompt does not use gaps at all.

**S03 gloss vs language_switch -- passed the hard part.** Cut `[13]` (language switch as
replacement), kept `[8]`/`[9]` (gloss that adds "so returns vary"). Missed one line of the r1 chain.

## Corrections to claims I had been repeating

**"Hinglish is our strongest language (recall 1.00, 0 false cuts)."** Wrong. On this set Hinglish
is the **weakest** -- 0.81 recall and both remaining false cuts. My synthetic Hinglish was too easy
and I had been reporting its result as a strength.

**"gpt-4o-mini recall 0.000, the task is beyond it."** Wrong. Raw responses show valid JSON with
real groups every time; `response_format: json_object` makes fence-parse failure impossible.
Re-measured on my set: mini 0.76 recall / 6 false cuts vs 4o 0.88 / 3. Pin 4o -- but because mini
deletes twice as much real content, not because it cannot do the task.

**"precision 1.000, zero false cuts over 5 runs."** True only on my old, easier set.

## Also worth knowing

- **Sampling noise is +-1 false cut and +-0.06 recall** between runs of the same config. Do not
  trust smaller differences, including several I quoted earlier.
- **Every prompt variant I tried lost.** Pure-intent framing with no category lists returned ZERO
  groups on all 12 of my scripts (recall 0.00); adding one sentence about line distance flipped it
  to the most aggressive variant tested (5 false cuts). A rigorous externally-written spec built on
  disfluency theory scored 0.17 on real footage, because its evidence floor required gap >= 0.7s and
  0.0% of post-silence-trim lines clear that.
- **The filter is not yet in `pipeline.py`.** It exists only in the test harness.
