"""Reel Editor — one function that runs the whole Vex + pycaps flow with
selectable steps. Powers the web UI (app.py) and is callable directly.

Steps (each optional, chosen by the caller):
  transcribe (ElevenLabs Scribe / Sarvam / Whisper)  ->  Hinglish romanize (OpenAI/Gemini)
  ->  remove silences  ->  natural color grade  ->  pycaps word-by-word captions  ->  export 1080x1920

Depends on the vendored modules in pipeline/vendor/ (a trimmed copy of the Vex engine).
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid

VEX = pathlib.Path(__file__).resolve().parent / "vendor"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "videos" / "reel-studio-out"

# Vendored engine/color-grading modules (see pipeline/vendor/) — no chdir needed, none of
# them read cwd-relative paths.
sys.path.insert(0, str(VEX))
try:
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env"))
except Exception:
    pass


def _build_whisper_json(segments: list[dict], path: pathlib.Path) -> int:
    wj = {"text": " ".join(str(s.get("text", "")) for s in segments), "language": "hi", "segments": []}
    for i, s in enumerate(segments):
        wj["segments"].append({
            "id": i, "start": float(s["start"]), "end": float(s["end"]), "text": s["text"],
            "words": [{"word": str(w["text"]), "start": float(w["start"]), "end": float(w["end"]),
                       "probability": 1.0} for w in s.get("words", [])],
        })
    path.write_text(json.dumps(wj, ensure_ascii=False, indent=1))
    return sum(len(s["words"]) for s in wj["segments"])


def _clean_audio(video_in: str, video_out: str) -> str:
    """Light source-audio cleanup before transcription. Returns the method used.

    Prefers a DeepFilterNet `deep-filter` binary if on PATH (or DEEPFILTER_BIN);
    otherwise a light ffmpeg chain (highpass 80Hz + FFT denoise + gentle compression).
    The video stream is copied untouched.
    """
    import tempfile
    df = shutil.which(os.getenv("DEEPFILTER_BIN", "deep-filter"))
    if df:
        with tempfile.TemporaryDirectory() as td:
            tdp = pathlib.Path(td)
            wav_in = tdp / "in.wav"
            subprocess.run(["ffmpeg", "-y", "-i", video_in, "-vn", "-ar", "48000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(wav_in)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run([df, str(wav_in), "--output-dir", td], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            outs = sorted(tdp.glob("*DeepFilter*.wav")) or [wav_in]
            subprocess.run(["ffmpeg", "-y", "-i", video_in, "-i", str(outs[0]),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", video_out],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return "DeepFilterNet"
    subprocess.run(["ffmpeg", "-y", "-i", video_in,
                    "-af", "highpass=f=80,afftdn,acompressor=threshold=-20dB:ratio=4",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", video_out],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return "ffmpeg(highpass80+afftdn+acompressor)"


def _hook_trim(state, note) -> float:
    """Conservatively trim slow preamble so the video starts on the strongest line.

    Uses gpt-4o-mini on the transcript to pick the opening line, trims the video START,
    and OFFSETS the transcript (segments.json) so captions stay synced. Never cuts more
    than the first 3 segments. Returns seconds trimmed (0.0 if it already starts strong).
    """
    import re as _re
    from tools.transcript import _openai_text  # reuses OPENAI_API_KEY

    wd = pathlib.Path(state.working_dir)
    seg_path = wd / "transcript.segments.json"
    segs = json.loads(seg_path.read_text())
    if len(segs) < 3:
        return 0.0
    head = segs[: min(8, len(segs))]
    numbered = "\n".join(f"{i + 1}. [{float(s['start']):.1f}s] {s['text']}" for i, s in enumerate(head))
    prompt = (
        "You are trimming the opening of a short vertical video so it starts on the strongest HOOK.\n"
        "Below are the first spoken lines with timestamps. Return ONLY the line NUMBER to start from.\n"
        "Cut only obvious slow preamble (greetings, throat-clearing, 'so...', channel intros). If it "
        "already opens strong, return 1. Be conservative — never cut real content.\n\n" + numbered
    )
    raw = _openai_text(prompt, timeout_s=30)
    m = _re.search(r"\d+", raw or "")
    start_idx = (int(m.group()) - 1) if m else 0
    start_idx = max(0, min(start_idx, 3, len(segs) - 1))  # conservative cap
    # Safety guard: only trim if the cut text actually looks like preamble / greeting /
    # filler. The LLM sometimes mistakes real opening content for preamble; never cut it.
    if start_idx > 0:
        cut_lower = " ".join(str(s.get("text", "")) for s in segs[:start_idx]).lower()
        markers = ("hello", "hi ", "hey", "welcome", "guys", "channel", "subscribe",
                   "namaste", "dosto", "my name", "in this video", "today i", "so ",
                   " um ", " uh ", "what's up", "kaise ho", "aaj hum", "aaj main", "friends")
        if not any(m in cut_lower for m in markers):
            return 0.0
    if start_idx == 0:
        return 0.0
    T = max(0.0, float(segs[start_idx]["start"]) - 0.15)
    if T < 0.4:
        return 0.0
    cut_text = " ".join(str(s["text"]) for s in segs[:start_idx]).strip()

    working = state.working_file
    trimmed = str(pathlib.Path(working).with_name(pathlib.Path(working).stem + "_hook.mp4"))
    subprocess.run(["ffmpeg", "-y", "-ss", f"{T:.3f}", "-i", working, "-c:v", "libx264",
                    "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k",
                    "-avoid_negative_ts", "make_zero", trimmed],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    state.working_file = trimmed
    state.save()

    new_segs = []
    for s in segs:
        if float(s["end"]) <= T:
            continue
        ns = dict(s)
        ns["start"] = max(0.0, float(s["start"]) - T)
        ns["end"] = max(0.0, float(s["end"]) - T)
        ns["words"] = [{**w, "start": max(0.0, float(w["start"]) - T), "end": max(0.0, float(w["end"]) - T)}
                       for w in s.get("words", []) if float(w["end"]) > T]
        if ns["words"] or str(ns.get("text", "")).strip():
            new_segs.append(ns)
    seg_path.write_text(json.dumps(new_segs, ensure_ascii=False, indent=1))
    note(f"  trimmed {T:.1f}s of preamble -> \"{cut_text[:60]}\"")
    return T


# =========================== retake detection (pre-filter -> detect -> judge panel) ===========================
def _pack_transcript_lines(segs: list[dict]) -> str:
    lines = []
    for i, s in enumerate(segs):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"{i + 1}. [{float(s['start']):.2f}-{float(s['end']):.2f}s] {text}")
    return "\n".join(lines)


# Generic English self-correction phrases -- content/topic-agnostic by construction (none of
# these reference what the video is ABOUT, only how a speaker signals "that wasn't right, let
# me redo it"), so this list works the same for a cooking video, a tech demo, or a vlog.
_RETAKE_MARKERS = (
    "let me restart", "let me redo", "let me try that again", "let me say that again",
    "scratch that", "start over", "let me start again", "take two", "let me redo this",
    "let me re-say", "ignore that", "strike that", "let me re-do",
)


def _has_retake_marker(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _RETAKE_MARKERS)


# NOTE: an earlier version of this file also gated the LLM detector behind a mechanical
# text-similarity/shared-prefix pre-filter (skip the LLM call entirely if no pair of nearby
# lines looked similar enough). Measured directly against real examples, that turned out to be
# unsafe: a genuine low-overlap paraphrase retake ("a really hot oven, like five hundred
# degrees" / "getting your oven as hot as it can possibly go") scored LOWER on pure lexical
# similarity (0.28) than two UNRELATED lines that just happened to share a sentence template
# ("The best camera for beginners is..." / "The best light for beginners is...", 0.39). Pure
# mechanical similarity can't separate those classes, so a threshold gate would have silently
# dropped real retakes to save one cheap LLM call. Removed -- the detector below always runs
# (same cost profile as this pipeline's existing always-on hook-trim/zoom LLM calls); only the
# marker check survived measurement as reliable, so it's kept as a HINT to the detector, never
# as a filter that could suppress a real candidate.


_RETAKE_DETECT_SYSTEM = (
    "You are proposing CANDIDATE retakes in a single continuous recording -- places where the "
    "speaker may have stopped and redone a line. This is only a first pass; a separate, "
    "stricter check decides what actually gets cut, so propose anything that looks plausible -- "
    "it's fine to be wrong here, don't try to be certain.\n\n"
    "Below is the full numbered, timestamped transcript. Group line numbers that look like "
    "different attempts at the same point into a retake group. For each group, pick which line "
    "number to KEEP -- normally the last attempt, unless an earlier one clearly reads as the "
    "intended final take.\n\n"
    'Return ONLY JSON: {"groups": [{"keep": <line number>, "cut": [<line numbers>], '
    '"reason": "..."}]}. If nothing looks like a retake, return {"groups": []}.'
)

# Selected by measurement, not intuition: scored against a 10-script / 76-line labelled eval set
# spanning 10 unrelated topics (18 genuine retakes vs 58 must-keep lines, the latter including
# rhetorical-repetition, shared-stem list, callback, refrain, bookend, contrast-pair, quotation
# and question-then-answer decoys). Zero false cuts across 5 full runs at recall ~0.76, versus
# ~0.72 for the previous wording. Two other variants scored higher recall (0.86) but cut real
# content on the refrain/bookend decoys, so they were rejected -- never cutting real content is
# the hard constraint, recall is the soft one.
#
# The CONFIRM clauses map to established self-repair categories (repetition, fragment+completion,
# marked repair with an editing term, substitution/error repair); the REJECT clauses map to the
# rhetorical-figure and discourse-structure classes those are most often confused with. Both are
# stated as general linguistic categories, never as content from any particular video -- keep it
# that way, and re-run the eval before changing anything here.
_RETAKE_JUDGE_SYSTEM = (
    "You are deciding whether to CUT a line from someone's video because it's a discarded "
    "retake -- an earlier, abandoned attempt at what a later KEEP line says.\n\n"
    "Cutting content the creator wanted is a serious mistake; leaving a retake in is a minor "
    "inconvenience. So when the evidence is genuinely balanced, REJECT.\n\n"
    "But apply that caution to real ambiguity, not to clear-cut cases. CONFIRM when any of these "
    "holds:\n"
    "  - The CUT line is a verbatim or near-verbatim repeat of the KEEP line. A repeat carries no "
    "information the KEEP line doesn't already carry, so nothing is lost by removing it.\n"
    "  - The CUT line is an incomplete fragment of what the KEEP line says in full (including a "
    "line broken off mid-word or mid-clause).\n"
    "  - The CUT line contains an explicit self-interruption ('wait', 'sorry', 'scratch that', "
    "'let me start over', 'hang on') and the KEEP line covers the same ground. The speaker has "
    "stated they are discarding it; take them at their word even if the wording differs.\n"
    "  - Both lines state the same fact, value or claim, and the KEEP line supersedes it -- "
    "including when they differ ONLY in a specific number, name or quantity. That difference is "
    "the correction itself, not a reason to keep both.\n\n"
    "REJECT when the CUT line does real work the KEEP line doesn't. Name which applies:\n"
    "  - it makes a genuinely different point, or is about a different subject\n"
    "  - it is deliberate repetition for rhetorical effect or emphasis\n"
    "  - it is a callback to, or a deliberate reprise of, something said earlier\n"
    "  - it is one entry in a list or sequence where each entry is distinct\n"
    "  - it is one half of a deliberate pair (question then answer, contrast, setup then payoff)\n"
    "  - it is quoting or attributing something to someone else\n\n"
    'Return ONLY JSON: {"confirmed": true|false, "reason": "..."}'
)


# Retake detection/judging runs on a DIFFERENT (stronger) model than the rest of this file's
# LLM work, and deliberately does NOT read OPENAI_MODEL -- that var is shared with romanization,
# zoom planning and the chat agent, which are fine on the cheap model and shouldn't get more
# expensive as a side effect. Measured on a 10-script / 76-line labelled eval set spanning 10
# unrelated topics (18 genuine retakes, 58 must-keep lines including rhetorical-repetition,
# list, callback, bookend and quotation decoys):
#     gpt-4o-mini  -> recall 0.000  (caught ZERO of 18 retakes; the task is beyond this model)
#     gpt-4o       -> recall 0.76, precision 1.000, zero false cuts over 5 full runs
# Override with RETAKE_MODEL if a cheaper/newer model ever needs to be swapped in, but re-run
# the eval first -- mini's failure here was total, not marginal.
_RETAKE_MODEL = os.getenv("RETAKE_MODEL", "").strip() or "gpt-4o"


def _openai_json(system: str, user: str, timeout_s: float, temperature: float = 0.2,
                 model: str | None = None, max_attempts: int = 4) -> tuple[dict | None, str | None]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None, "OPENAI_API_KEY not set"
    model = model or os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    import random
    import time

    import requests
    # Retry 429/5xx with backoff. Without this a rate-limited judge call returns "reject" (see
    # _retake_judge_once's fail-closed default), so hitting the API's rate limit silently turned
    # retake detection off with no visible error -- measured during eval, where a burst of 429s
    # dropped recall from 0.78 to 0.00 while still reporting a clean, plausible-looking result.
    delay = 1.5
    for attempt in range(max_attempts):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                timeout=timeout_s,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == max_attempts - 1:
                    return None, f"{resp.status_code} from OpenAI after {max_attempts} attempts"
                time.sleep(delay + random.uniform(0, 1.0))
                delay = min(delay * 2, 20)
                continue
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"]), None
        except Exception as e:
            if attempt == max_attempts - 1:
                return None, str(e)[:160]
            time.sleep(delay + random.uniform(0, 1.0))
            delay = min(delay * 2, 20)
    return None, "retries exhausted"


def _retake_detector(numbered: str) -> tuple[list[dict], str | None]:
    """LLM pass 1: propose candidate retake groups from the numbered transcript. Low temperature
    -- this pass just needs to be a consistent, well-structured proposal; the judge PANEL below
    (higher temperature, run 3x) is where reliability-through-diversity actually matters."""
    data, err = _openai_json(_RETAKE_DETECT_SYSTEM, "Transcript:\n" + numbered, timeout_s=60,
                             temperature=0.2, model=_RETAKE_MODEL)
    if err:
        return [], err
    return (data.get("groups") or []) if isinstance(data, dict) else [], None


def _retake_judge_once(user: str) -> bool:
    """One adversarially-framed re-check of a candidate group. Defaults to REJECT on any
    parse/API failure -- an unverified claim never cuts. temperature=0.7 (vs. the detector's
    0.2) so 3 independent calls can actually disagree -- a self-consistency vote over 3 near-
    identical deterministic calls would just be the same answer 3x for no added reliability.

    `user` is a RENDERED CONTEXT BLOCK, not two bare strings. It used to be exactly
    'KEEP: "..."' + 'CUT: "..."' with no line numbers and no neighbours, which made six of the
    eight REJECT categories in the prompt unanswerable: callback, list item, bookend, setup/payoff,
    keyword echo and teaching recap are all POSITIONAL properties, and the judge could not see
    position. Measured consequence -- it confirmed 16 of 16 candidates on real footage, and every
    false cut on the eval set was a case decidable only from context (an intro catchphrase matched
    to the outro 8 lines away; a rhetorical echo following a completed sentence). Three votes on a
    blindfolded question agree because the input is deterministic, not because the answer is right."""
    data, err = _openai_json(_RETAKE_JUDGE_SYSTEM, user, timeout_s=30, temperature=0.7,
                             model=_RETAKE_MODEL)
    if err or not isinstance(data, dict):
        return False
    return bool(data.get("confirmed"))


_JUDGE_CONTEXT_LINES = 5   # either side; enough to see whether a list or a pair continues


def _render_judge_context(segs: list[dict], keep: int, cuts: list[int]) -> str:
    """The judge's user message: the candidate IN PLACE, with numbered neighbours.

    Line numbers are the point -- they are what make adjacency, distance and
    opens-the-video/closes-the-video observable at all."""
    lo = max(1, min([keep] + list(cuts)) - _JUDGE_CONTEXT_LINES)
    hi = min(len(segs), max([keep] + list(cuts)) + _JUDGE_CONTEXT_LINES)
    out = [f"Transcript has {len(segs)} lines. Showing {lo}-{hi}.", ""]
    for i in range(lo, hi + 1):
        tag = "CUT? " if i in cuts else ("KEEP " if i == keep else "     ")
        out.append(f"{tag}[{i:03d}] {str(segs[i - 1].get('text', '')).strip()}")
    out += ["", f"Decide: are line(s) {sorted(cuts)} discarded attempts at what line {keep} says?"]
    return "\n".join(out)


def _retake_judge_panel(user: str, n: int = 3) -> tuple[bool, int, int]:
    """3 independent judge calls IN PARALLEL, majority vote -- self-consistency, not one
    fallible pass. A single judge measurably rejected an obvious 'wait, let me restart' case in
    testing; a 2-of-3 majority is far less likely to hinge on one call's specific phrasing.
    Returns (confirmed, votes_for, n) -- the vote margin becomes the confidence shown in the
    Fine-tune "Trimmed" review sheet (3/3 = high, 2/3 = medium), instead of throwing it away."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=n) as ex:
        votes = list(ex.map(lambda _: _retake_judge_once(user), range(n)))
    votes_for = sum(votes)
    return votes_for > n / 2, votes_for, n


# A cut spans exactly the WORDS being removed -- first word's start to last word's end -- with no
# padding and no guard band. Both were tried and measured against a real 17-cut edit and both made
# things worse:
#   * padding (expand the cut by 0.12s each side) plus a 0.15s guard held back from neighbours
#     produced 8 orphan slivers of 0.10-0.32s containing ZERO words -- audible fragments between
#     two cuts that should simply have been one cut -- and 10 cut edges slicing through the middle
#     of words (at 7%, 50%, 73%, 90% through), because the guard pushes an edge INTO the very word
#     it is removing whenever the neighbouring gap is tighter than the guard.
#   * word bounds with no padding at all: 0 slivers, 0 mid-word edges, and 17 ledger entries
#     coalesce into 9 clean ranges.
# It works because word timings already sit inside the surrounding pause: cutting at a word's own
# start/end automatically preserves the whole inter-word silence on both sides, which is exactly
# what the padding was trying (and failing) to buy. Trailing-consonant protection is therefore
# free, and no arbitrary time constant is involved.
def _cut_range_for_segment(segs: list[dict], i: int) -> tuple[float, float]:
    """Exact word-bounded span of segment `i`, falling back to its own bounds if it has no words."""
    ws = segs[i].get("words") or []
    if ws:
        return float(ws[0]["start"]), float(ws[-1]["end"])
    return float(segs[i]["start"]), float(segs[i]["end"])


def _coalesce_wordless_gaps(merged: list[list[float]], segs: list[dict]) -> list[list[float]]:
    """Join two cuts whose intervening gap contains no surviving words. Such a gap is pure
    leftover audio -- a breath or half a syllable -- and leaving it produces the 'tiny weird
    fragment between two cuts' artifact. Merging is strictly safe: nothing spoken is lost."""
    if len(merged) < 2:
        return merged
    words = [w for s in segs for w in (s.get("words") or [])]
    out = [merged[0]]
    for a, b in merged[1:]:
        survives = any(float(w["start"]) >= out[-1][1] - 0.02 and float(w["end"]) <= a + 0.02
                       for w in words)
        if not survives:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


# Overhang trimming. A word's acoustic extent is wider than its ASR timestamps -- measured on a
# real seam, the removed line's label START was 80ms late and its label END 60ms early, so cutting
# on word bounds left ~140ms of it audible right before the kept take ("so it's not la-- so it's
# not lying on purpose"). These three constants make removing that overhang safe:
_MAX_EDGE_EXPAND = 0.35   # never move an edge further than this; keeps a bug bounded
_QUIET_RUN = 0.10         # this much CONSECUTIVE quiet = real silence rather than a gap INSIDE a
                          # word. Measured, not reasoned: swept 100/120/160/200ms x 6 threshold
                          # estimators over 41 real cuts from 6 sources, scored by how much
                          # DETACHED audible fragment survives at the seams (see below). 100ms won
                          # at every threshold, and by a wide margin -- 6/41 cuts with a fragment
                          # vs 24/41 at 200ms.
                          #
                          # This contradicts the phonetics argument that set it to 200ms: a
                          # word-final stop closure is 95-140ms of true silence, so a 100ms quiet
                          # run can sit INSIDE a word, and 200ms was chosen to clear
                          # closure+burst+aspiration. That argument is sound but optimises the
                          # wrong thing. Demanding 200ms of contiguous quiet means the scan finds
                          # nothing on ~27% of flanks -- the speaker ran straight from the kept
                          # word into the retake with under 200ms of space, so no such run exists
                          # -- and every failure VETOES the trim, leaving the whole fragment
                          # audible. Trading a rare mid-closure seam (inaudible, and it can only
                          # ever land inside the REMOVED word -- the kept-anchor design makes
                          # crossing a kept word structurally impossible) against a frequent
                          # total veto is strongly net-positive.
                          #
                          # At 200ms the trim was measurably WORSE THAN NOT TRIMMING AT ALL
                          # (24/41 fragments and 7160ms vs 20/41 and 4060ms for raw word bounds),
                          # which is why the stutter survived every previous attempt to tune it.
_MAX_RETAKE_SPAN = 8      # widest first-attempt-to-good-take block span-fill will trust. A real
                          # retake block is a handful of lines; anything wider means the detector
                          # paired lines that aren't one block, and filling it would delete content.
_KEEP_MARGIN = 0.03       # stay this far off a KEPT word's boundary, so the later frame snap
                          # (up to ~33ms outward) can never push an edge into kept speech.
_PAUSE_KEEP = 0.25        # silence handed back at each seam instead of deleting the whole gap.
                          # Deleting the gap outright is what made an earlier attempt strip 13.4s and
                          # flatten the delivery, so some has to come back.
                          #
                          # THIS KNOB IS NEARLY INERT -- do not reach for it to shorten a seam. The
                          # giveback is clamped to run_start (see _trim_overhang), and the quiet run
                          # is always exactly _QUIET_RUN long by construction, so any value above
                          # _QUIET_RUN lands on the same clamp. Measured: 0.25 and 0.15 produce
                          # byte-identical cuts on real projects (11/139ms and 31/260ms of surviving
                          # pause either way). Its whole range of authority is ~10ms.
                          #
                          # What actually sets the surviving pause is _QUIET_RUN plus the gap between
                          # where silence ends and where the next kept word is LABELLED to start --
                          # and that second part is the next word's own onset, so it cannot be cut
                          # without clipping speech. The pause at a seam is therefore close to
                          # irreducible, which is why leftover gesture is MASKED (_seam_mask_zooms in
                          # server.py) rather than trimmed away.

# NOTE on render-side seam lateness: measured by raw-sample correlation, the rendered seam lands
# up to ~140ms later than the range it was given (a cut computed at 9.632s produced output still
# matching the source verbatim through 9.72). The range arithmetic is provably right -- total
# duration matches prediction to ~10ms -- so the slip is in trim/concat's frame- vs
# sample-quantised piece durations. An explicit pre-compensation constant was tried and is no
# longer needed: the kept-anchor design below puts each seam inside a >=200ms run of silence, so a
# 140ms slip still lands in silence. It costs a little of the handed-back pause, nothing audible.


def _audio_envelope(video_path: str, sr: int = 16000, win: float = 0.02):
    """(per-window bool "is this window quiet" mask, window seconds, unused). Word timings say WHICH
    words to remove; only the waveform can say where the sound actually stops.

    Full-band only. A band-limited 1.5-6kHz pass was tried here on the theory that sibilants, stop
    bursts and aspiration are high-frequency and low-energy and so barely move a full-band RMS
    reading. Measurement killed it: at a clear vowel onset (full-band -31.5dB) that band reads
    -60.8dB, QUIETER than the pause 200ms earlier (-50.7dB), because a vowel attack has little
    1.5-6kHz energy. It does not separate speech from silence on this material in either direction
    -- ANDed it vetoed every cut, maxed it inflated quiet regions by 20dB. Full-band alone
    separates cleanly here: pause -47..-54dB against speech -31..-36dB."""
    try:
        import numpy as np
    except ImportError:
        return None, win, 0.0

    def decode(extra_af: list[str]):
        try:
            return subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(video_path), "-ac", "1", "-ar", str(sr),
                 *extra_af, "-f", "f32le", "-"],
                capture_output=True, check=True, timeout=300).stdout
        except Exception:
            return None

    raw = decode([])
    if raw is None:
        return None, win, 0.0
    x = np.frombuffer(raw, dtype=np.float32)
    n = int(win * sr)
    if x.size < n * 4:
        return None, win, 0.0
    k = x.size // n

    def rms_db(sig):
        return 20.0 * np.log10(np.sqrt((sig[: k * n].reshape(k, n) ** 2).mean(axis=1)) + 1e-9)

    env = rms_db(x)
    # SPEECH-RELATIVE gate: 17dB below the 75th-percentile level. The obvious alternative -- anchor
    # on the noise floor as a low percentile plus a margin -- is what was here, and it is wrong on
    # this material. These clips are already silence-trimmed, so the low percentiles sit in the
    # splice gaps rather than in room tone (p1 = -84dB, p5 = -58dB on a file whose actual room tone
    # is ~-50dB), which drags the threshold far too low and makes real pauses read as speech. A
    # high percentile lands in speech reliably -- speech is most of a silence-trimmed clip -- so
    # offsetting down from it tracks each recording's own level. Measured p75-17 across 6 sources:
    # -42.0..-39.2dB, a 3dB spread, versus a 11dB spread for the noise-floor form. This is the
    # standard shape for dialogue gating (EBU R128 gates 10 LU under ungated loudness; practical
    # noise gates sit 15-25dB under signal). Clamp is a guard for pathological input and does not
    # bind on any file measured.
    quiet = env <= max(min(float(np.percentile(env, 75)) - 17.0, -35.0), -50.0)
    return quiet, win, 0.0


def _starts_sentence(text: str) -> bool:
    """Could this line be the START of a spoken take, rather than the continuation of one?

    Capitalisation only. Treating clause-joining words ("and", "so", "but") as continuations was
    tried and is wrong for speech: "So next time your AI reads an email..." opens a sentence, and
    demoting it walked the take boundary back over real content. A lowercase opening word is the one
    reliable signal that a line began mid-sentence.

    Deliberately permissive -- anything not clearly mid-sentence counts as a start -- so an
    unreliable reading keeps a line rather than cutting one."""
    t = text.strip().lstrip("\"'([-—– ")
    if not t:
        return True
    c = t[0]
    return not (c.isalpha() and c.islower())


def _trim_overhang(ranges: list[list[float]], segs: list[dict], quiet, win: float) -> list[list[float]]:
    """Place each cut inside the SILENCE that flanks the removed line, anchored on the kept words.

    The trick -- and the thing three earlier attempts of mine got wrong -- is to stop trying to
    locate the removed word's own acoustic edge. That edge is the hard problem: its tail and the
    next kept word's onset are both just "loud", and guessing wrong deletes real speech (an early
    version deleted the word "not" from "so it's not lying on purpose"). Instead:

      * Anchor on the neighbouring KEPT words' labelled edges and expand OUTWARD only. The cut can
        then never reach a kept word, by construction rather than by tuning -- assertable, not
        hoped for.
      * Scan out from those anchors for the first place where _QUIET_RUN of real silence begins
        (start side) / ends (end side). That lands the seam inside the pause, past the removed
        line's tails, without ever needing to know where they are.
      * Leave _PAUSE_KEEP of that silence behind instead of deleting the whole gap. Deleting it is
        what made an earlier attempt remove 13.4s too much and wreck the pacing: an inter-line
        pause is legitimately quiet all the way through, so nothing stopped the scan. Keeping ~250ms
        preserves the breath between lines (200ms+ is where a silence starts reading as a pause at
        all, and ~0.6s rates most natural) while still removing every trace of the retake.
      * If neither flank has a real silence to cut in, VETO: leave the original word-bounded range
        alone rather than force a seam into speech.
    """
    if quiet is None or not ranges:
        return ranges
    words = [(float(w["start"]), float(w["end"])) for s in segs for w in (s.get("words") or [])]
    # Only surviving words constrain us; a word inside another cut range is harmless to cross.
    kept = sorted((ws, we) for ws, we in words
                  if not any(a <= (ws + we) / 2 < b for a, b in ranges))
    run = max(1, int(_QUIET_RUN / win))

    def silent_from(i: int) -> bool:
        return i >= 0 and i + run <= len(quiet) and bool(quiet[i:i + run].all())

    out = []
    for a, b in ranges:
        prev_end = max([e for _, e in kept if e <= a + 1e-6], default=0.0)
        next_start = min([s for s, _ in kept if s >= b - 1e-6], default=b)
        # START: first point at/after the previous kept word where real silence begins.
        t_prev, i, limit = None, int(prev_end / win), int(a / win)
        while i <= limit:
            if silent_from(i):
                t_prev = i * win
                break
            i += 1
        # END: last point at/before the next kept word where real silence ends.
        t_next, run_start, i, limit = None, None, int(next_start / win) - run, int(b / win)
        while i >= limit:
            if silent_from(i):
                run_start, t_next = i * win, (i + run) * win
                break
            i -= 1
        if t_prev is None and t_next is None:
            out.append([max(a, 0.0), max(b, a + 0.01)])   # veto: nowhere clean to cut on either side
            continue
        # Per-side, not all-or-nothing. Roughly two thirds of the flanks with no usable silence have
        # one on the OTHER side, and an earlier version discarded that good side along with the bad
        # one, leaving the whole fragment audible when half of it was removable.
        new_a = a if t_prev is None else max(t_prev + _KEEP_MARGIN, prev_end, 0.0)
        if t_next is None:
            out.append([max(new_a, 0.0), max(b, new_a + 0.01)])
            continue
        new_b = min(t_next - _KEEP_MARGIN, next_start)
        # Hand back a pause. Taken off the END so the silence sits immediately before the kept
        # line, which is where a natural breath belongs.
        #
        # Clamped to run_start, and that clamp is the whole ballgame. The giveback is only allowed
        # to walk back through silence we actually measured. Without the clamp it walks back a flat
        # _PAUSE_KEEP, and whenever the quiet run is shorter than that it overshoots the run and
        # re-exposes the removed take's tail -- which is precisely the "so it's not la..so it's not
        # lying on purpose" stutter: the scan found the pause correctly, then the giveback handed
        # 60ms of the discarded attempt back across the seam.
        new_b = max(new_a + 0.01, min(new_b, max(new_b - _PAUSE_KEEP, run_start)))
        if new_b <= new_a:
            new_a, new_b = a, b
        out.append([max(new_a, 0.0), max(new_b, new_a + 0.01)])
    return out


def _frame_times(video_path: str) -> list[float]:
    """Every real frame presentation time in the file. Phone footage is variable-frame-rate (the
    checkpoint measured here reported a 31fps container against a 30.75fps actual average), so
    'one frame' is not a fixed step and has to be read from the stream.

    Packet timestamps, not frame timestamps: `frame=best_effort_timestamp_time` decodes the entire
    video (29.0s on a 249s clip, so minutes on a long one -- the old 180s timeout here would have
    expired and silently returned [], disabling frame snapping on long videos). Packet PTS need no
    decode and are the same values: verified identical on real footage, 7503 timestamps, max
    difference 0.0ms, 0.13s versus 29s."""
    def read(entries: str, timeout: int) -> list[float]:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", entries, "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=timeout).stdout
        except Exception:
            return []
        ts = []
        for line in out.splitlines():
            v = line.strip().rstrip(",")
            if v:
                try:
                    ts.append(float(v))
                except ValueError:
                    pass
        return sorted(ts)

    ts = read("packet=pts_time", 120)
    return ts if len(ts) >= 2 else read("frame=best_effort_timestamp_time", 600)


def _snap_ranges_to_frames(ranges: list[list[float]], frames: list[float]) -> list[list[float]]:
    """Snap each cut OUTWARD to real frame boundaries: start back to the frame at/before it, end
    forward to the frame at/after it. Extracting a segment on arbitrary times makes ffmpeg round
    the output up to a whole frame -- measured at +31ms per cut, which accumulated to +308ms of
    drift over 17 cuts. Because captions are remapped by exact arithmetic, that drift showed up as
    subtitles sliding progressively out of sync with the speech. Snapped boundaries measured
    +0.1ms. Both edges move into the surrounding silence (never into kept speech), by at most one
    frame (~32ms), which is far smaller than any inter-word gap."""
    if not frames:
        return ranges
    snapped = []
    for a, b in ranges:
        lo = [t for t in frames if t <= a + 1e-6]
        hi = [t for t in frames if t >= b - 1e-6]
        snapped.append([lo[-1] if lo else frames[0], hi[0] if hi else frames[-1]])
    return snapped


def _merge_cut_ranges(ranges: list[tuple[float, float]]) -> list[list[float]]:
    """Sort + coalesce overlapping/adjacent cut ranges. Shared by the cut applier and the
    remappers below so all three always agree on what the effective removed set is."""
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if out and a <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([max(a, 0.0), max(b, 0.0)])
    return out


def remap_to_output(t: float, merged: list[list[float]]) -> float:
    """SOURCE time -> OUTPUT time, given the cut ranges removed from the source. Used to place
    a cut's marker on the already-rendered video's own timeline, and to keep the transcript in
    sync after cutting."""
    removed_before = sum(min(b, t) - a for a, b in merged if a < t)
    return max(0.0, t - removed_before)


def remap_to_source(t_out: float, merged: list[list[float]]) -> float:
    """OUTPUT time -> SOURCE time: the inverse of remap_to_output. Needed so a tap on the
    ALREADY-CUT (rendered) video can be translated back to the original checkpoint timeline,
    which is the only timeline the cut ledger is expressed in. Walks the removed ranges in
    order, re-adding each gap that falls at or before the running output position."""
    t_src = t_out
    for a, b in merged:  # merged is sorted, non-overlapping
        if a <= t_src:
            t_src += (b - a)
        else:
            break
    return max(0.0, t_src)


# Audio crossfade length at each splice. The reference implementation behind transcript-based
# editors (Rubin et al., UIST 2013) uses 5ms; audio practice puts the click-free band at 2-10ms and
# notes that anything past ~100ms becomes an audible blend. 8ms is inaudible and removes the click.
# vex's merge() concatenates with no fade at all, so every cut boundary was a potential pop -- and
# the only crossfades in this codebase were the 120-150ms ones on B-roll overlays, which never
# touched speech splices.
_SPLICE_FADE = 0.008


def _extract_with_splice_fades(video_path: str, working_dir: str,
                               keep_ranges: list[tuple[float, float]]) -> str:
    """Keep only `keep_ranges` and concatenate them in ONE ffmpeg pass, with a short audio fade
    at both ends of every piece so splices don't click.

    Replaces vex's extract_segments() for this path, which trimmed each range to its own file,
    re-encoded every one to normalise for concat, then concatenated -- two encodes per piece, no
    fades. Everything here comes from a single source file, so the pieces are already dimensionally
    identical and no normalisation pass is needed; the trim filter is also frame-exact, unlike
    seek-based extraction. Falls back to vex's implementation if the filtergraph fails, so a cut
    still happens rather than the whole edit being lost."""
    out = pathlib.Path(working_dir) / f"cut_{uuid.uuid4().hex[:12]}.mp4"
    has_audio, a_sr = False, 48000
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=60)
        rate = probe.stdout.strip()
        if rate:
            has_audio = True
            try:
                a_sr = int(float(rate))
            except ValueError:
                pass
    except Exception:
        pass

    parts, vlabels, alabels = [], [], []
    for i, (a, b) in enumerate(keep_ranges):
        d = b - a
        parts.append(f"[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]")
        vlabels.append(f"[v{i}]")
        if has_audio:
            fade = min(_SPLICE_FADE, max(d / 4.0, 0.001))  # never fade more than 1/4 of a short piece
            # Time-based selection, then an explicit sample-count clamp. Positioning must be
            # time-based: `start`/`end` follow PTS, which is the timeline the word timestamps live
            # in, whereas start_sample indexes the decoded stream and the two diverge on any input
            # whose audio carries padding. The clamp then guarantees the piece is exactly as long as
            # it was asked to be, so no per-piece excess can survive into the concat and accumulate.
            # Verified against a white-noise source by sample-exact cross-correlation: identical to
            # plain time-based on well-formed input (<=20ms total over 8 joins, all sub-frame), and
            # a hard stop on the accumulation seen when the input itself is padded.
            ns = max(1, int(round(d * a_sr)))
            parts.append(
                f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,"
                f"atrim=end_sample={ns},"
                f"afade=t=in:st=0:d={fade:.4f},afade=t=out:st={max(d - fade, 0):.6f}:d={fade:.4f}[a{i}]")
            alabels.append(f"[a{i}]")
    # One concat PER STREAM -- see extract_segments_single_pass in vex/engine.py for the
    # measurement. A joint concat=v=1:a=1 makes each piece's frame-rounded video duration govern its
    # audio too, and the rounding accumulates into audible drift over many pieces.
    n = len(keep_ranges)
    parts.append(f"{''.join(vlabels)}concat=n={n}:v=1:a=0[vout]")
    if has_audio:
        parts.append(f"{''.join(alabels)}concat=n={n}:v=0:a=1[aout]")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video_path),
           "-filter_complex", ";".join(parts), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", str(out)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return str(out)
    except Exception:
        from engine import extract_segments_single_pass
        return extract_segments_single_pass(video_path, working_dir, keep_ranges)


def _apply_retake_cuts(video_path: str, working_dir: str, segs: list[dict],
                        cut_ranges: list[tuple[float, float]]) -> tuple[str, list[dict]]:
    """The actual cut+remap mechanics, factored out so the initial auto-cut, a later
    "restore this one", and a user's own tap-marked trim all go through the exact same code
    path. `cut_ranges` are explicit (start_s, end_s) spans in `video_path`'s own timeline --
    NOT segment indices, so a cut can be any word-aligned span, not just a whole sentence.
    Returns (new_video_path, new_segs); does NOT touch a ProjectState, so it's equally usable
    against a live project or a cached checkpoint."""
    merged = _merge_cut_ranges(cut_ranges)
    if not merged:
        return video_path, segs
    # Order matters:
    #   1. coalesce cuts separated only by wordless leftovers (kills orphan fragments),
    #   2. trim each edge out to clear the removed line's own onset/tail, bounded (see
    #      _trim_overhang) -- this is what stops a clipped fragment of the removed take being
    #      audible just before the kept one,
    #   3. merge again, since trimming can make two ranges meet,
    #   4. snap to real frame boundaries LAST so the final edges stay frame-exact.
    merged = _coalesce_wordless_gaps(merged, segs)
    quiet, win, _ = _audio_envelope(video_path)
    merged = _merge_cut_ranges([tuple(r) for r in _trim_overhang(merged, segs, quiet, win)])
    merged = _merge_cut_ranges([tuple(r) for r in
                                _snap_ranges_to_frames(merged, _frame_times(video_path))])

    # The clip's real end. Must be the probed duration, NOT last_segment_end + slack: overshooting
    # makes the final keep range run past the video stream to whatever the longest stream reaches
    # (audio usually outlasts video by a frame or two), which showed up as a constant ~177ms of
    # extra tail. Harmless for sync, since it is all after the last spoken word, but it made the
    # arithmetic disagree with the file. Falls back to the transcript-derived estimate only when
    # probing fails.
    from engine import probe_video as _probe_v
    try:
        dur = float(_probe_v(video_path)["duration_sec"])
    except Exception:
        dur = (float(segs[-1]["end"]) + 1.0) if segs else 0.0

    keep_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in merged:
        if a > cursor:
            keep_ranges.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < dur:
        keep_ranges.append((cursor, dur))
    keep_ranges = [(a, b) for a, b in keep_ranges if b - a > 0.05]
    if not keep_ranges:
        return video_path, segs

    new_file = _extract_with_splice_fades(video_path, working_dir, keep_ranges)

    # Record where the seams land in the OUTPUT timeline, for the render to mask visually.
    # A word-bounded cut removes what was said but not the body language around it: gesture stroke
    # onset leads the spoken phrase by 200-500ms and its preparation starts earlier still, so the
    # speaker's hand is already rising for a sentence that no longer exists, and the seam jumps to a
    # different pose. Measured on a real seam, no better cut point existed within +-300ms -- the
    # pause simply isn't long enough to hide the gesture in -- so the movement can't be removed and
    # has to be masked instead. These positions let the renderer change framing exactly at the cut,
    # which reads as a deliberate edit rather than a glitch.
    try:
        seams, acc = [], 0.0
        for i, (ka, kb) in enumerate(keep_ranges):
            acc += kb - ka
            if i < len(keep_ranges) - 1:
                seams.append(int(round(acc * 1000)))
        (pathlib.Path(working_dir) / "retake_seams.json").write_text(json.dumps(seams))
    except Exception:
        pass  # masking is cosmetic; never fail a cut over it

    def _kept(t0: float, t1: float) -> bool:
        """A word/segment survives if its midpoint isn't inside any removed range."""
        mid = (t0 + t1) / 2.0
        return not any(a <= mid < b for a, b in merged)

    new_segs = []
    for s in segs:
        s_start, s_end = float(s["start"]), float(s["end"])
        words = [w for w in s.get("words", []) if _kept(float(w["start"]), float(w["end"]))]
        # Drop a segment only when nothing of it survives. A partially-cut segment keeps its
        # surviving words and tightens its own bounds to them, so a mid-sentence trim leaves
        # correct captions instead of either dropping the whole line or keeping stale timings.
        if not words and not _kept(s_start, s_end):
            continue
        ns = dict(s)
        ns["words"] = [{**w, "start": remap_to_output(float(w["start"]), merged),
                        "end": remap_to_output(float(w["end"]), merged)} for w in words]
        if ns["words"]:
            ns["start"] = ns["words"][0]["start"]
            ns["end"] = ns["words"][-1]["end"]
            ns["text"] = " ".join(str(w.get("text", "")).strip() for w in ns["words"]).strip() or s.get("text", "")
        else:
            ns["start"] = remap_to_output(s_start, merged)
            ns["end"] = remap_to_output(s_end, merged)
        new_segs.append(ns)

    # Clamp every remapped time to the file that actually exists. ASR returns timestamps past the
    # end of the media (measured: a checkpoint of 141.15s whose transcript's last segment ended at
    # 142.28s -- a known Whisper-family artifact), and remapping faithfully carried that overrun
    # into the output, leaving captions scheduled ~1s after the video finished. Clamping here fixes
    # it once for every consumer of the transcript instead of in each renderer.
    try:
        out_dur = float(_probe_v(new_file)["duration_sec"])
    except Exception:
        out_dur = None
    if out_dur:
        def _cl(t):
            return max(0.0, min(float(t), out_dur))
        for ns in new_segs:
            ns["words"] = [{**w, "start": _cl(w["start"]), "end": _cl(w["end"])} for w in ns.get("words", [])]
            ns["start"], ns["end"] = _cl(ns["start"]), _cl(ns["end"])
        new_segs = [ns for ns in new_segs if float(ns["end"]) - float(ns["start"]) > 0.01]
    return new_file, new_segs


def _cut_retakes(state, note) -> int:
    """Detects and removes re-recorded retakes (the speaker redoing the same line): a detector
    LLM proposes candidate retake groups from the transcript (with mechanically-detected
    self-correction phrases highlighted as hints, never as a filter), then an INDEPENDENT 3-judge
    panel (majority vote) adversarially re-checks each one before it's allowed to cut anything.
    Only whole transcript SEGMENTS are ever cut (never mid-word, never mid-segment), so remapping
    the transcript after the edit is exact -- captions stay in sync with no re-transcription.
    Works on any video because it reasons from what was actually said, not fixed timing/silence
    rules, and none of the detection signals reference this video's specific content.

    Saves a checkpoint (pre_retakes.mp4 + pre_retakes.segments.json) and a `retake_cuts.json`
    ledger (what was cut, why, and how confident the judge panel was) UNCONDITIONALLY -- even
    when zero retakes are found -- not just when something ends up cut. That's deliberate: the
    Fine-tune "Trimmed" sheet and the ready-screen summary need the checkpoint to report how
    much silence-trim ALONE removed on the (much more common) case where retake-detection finds
    nothing, which would otherwise silently report zero even though the video really did shrink.

    Returns the number of confirmed retake groups actually cut."""
    wd = pathlib.Path(state.working_dir)
    seg_path = wd / "transcript.segments.json"
    segs = json.loads(seg_path.read_text())
    n = len(segs)
    if n < 2:
        return 0

    pre_video = wd / "pre_retakes.mp4"
    checkpoint_ok = True
    try:
        shutil.copy2(state.working_file, pre_video)
        (wd / "pre_retakes.segments.json").write_text(json.dumps(segs, ensure_ascii=False, indent=1))
    except Exception:
        checkpoint_ok = False  # cutting still proceeds -- just isn't reportable/restorable later

    def _finish(ledger: list[dict]) -> int:
        if checkpoint_ok:
            (wd / "retake_cuts.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
        return len(ledger)

    numbered = _pack_transcript_lines(segs)
    marker_lines = [i + 1 for i, s in enumerate(segs) if _has_retake_marker(str(s.get("text", "")))]
    if marker_lines:
        numbered = (
            f"(Mechanical note: line(s) {marker_lines} contain an explicit self-correction "
            "phrase, e.g. 'let me restart' / 'scratch that' -- strong candidates, but confirm "
            "against the actual content same as any other line.)\n\n" + numbered
        )
    groups, err = _retake_detector(numbered)
    if err:
        note(f"  retake detection skipped ({err})")
        return _finish([])
    if not groups:
        return _finish([])

    cut_idx: set[int] = set()  # 1-based (line numbers), matches the detector's convention
    keep_idx: set[int] = set()
    ledger: list[dict] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        try:
            keep = int(g.get("keep"))
            cuts = [int(c) for c in (g.get("cut") or [])]
        except (TypeError, ValueError):
            continue
        if not (1 <= keep <= n):
            continue
        cuts = [c for c in cuts if 1 <= c <= n and c != keep]
        if not cuts:
            continue
        confirmed, votes_for, votes_total = _retake_judge_panel(
            _render_judge_context(segs, keep, cuts))
        if not confirmed:
            note(f"  judge panel rejected a candidate retake near line {keep} — keeping it.")
            continue
        # A take can span SEVERAL transcript lines, and the detector names only one as `keep`. When
        # the line it names is a mid-sentence continuation, the take really begins earlier -- and the
        # earlier half tends to get listed as a cut, beheading the good take. Real case: lines 38+39
        # were one take ("So next time your AI reads an email, books" + "something, or grabs live
        # data for you, MCP is"); the detector kept 39 and cut 38, so playback became "...connections
        # live right now. SOMETHING, OR GRABS LIVE DATA FOR YOU..." -- a sentence starting in its own
        # middle, which is a large part of what sounds like a repeated fragment.
        #
        # A line that does not begin a sentence cannot be the start of a take, so walk the block
        # boundary back over any such line the detector wanted to cut. Erring here keeps a line that
        # might have been removable, never the reverse, which is the required direction.
        cut_set = set(cuts)
        block_start = keep
        while (block_start - 1) in cut_set and not _starts_sentence(segs[block_start - 1].get("text", "")):
            block_start -= 1
        if block_start != keep:
            cuts = [c for c in cuts if c < block_start]
            if not cuts:
                note(f"  line {keep} continues the take at line {block_start} — nothing left to cut.")
                continue
        keep_idx.update(range(block_start, keep + 1))
        keep_text = segs[keep - 1].get("text", "")
        # SPAN-FILL: a retake block runs from the first abandoned attempt to the line before the
        # good take, so remove that whole span rather than only the lines the detector listed.
        #
        # Without this, a retake that spans two transcript lines gets half-removed. Real case:
        # lines 33 and 35-38 were all attempts at "So next time your AI reads your emails, book
        # something...", line 39 was the good take, and line 34 was "something for you." -- the
        # tail of abandoned attempt 33. The detector cut 33 but not 34, so playback became
        # "...connections live right now. SOMETHING FOR YOU. So next time your AI reads an
        # email...". Same shape orphaned "OpenAI all boarded in." (line 26, tail of cut line 25).
        # That dangling remainder is what reads as "it repeats a bit of the trimmed part": the
        # audio splice is exact, the wrong set of lines was chosen.
        #
        # Filling the span is safe because the material between attempts at the same sentence is,
        # by definition, more of those attempts -- there is no new content to lose. It stays bounded
        # by the detector's own group (never past `keep`), any line another group designated as a
        # keeper is subtracted below, and an implausibly wide span is left alone rather than trusted.
        lo = min(cuts)
        if block_start > lo and (block_start - lo) <= _MAX_RETAKE_SPAN:
            cuts = list(range(lo, block_start))
        cut_idx.update(cuts)
        reason = str(g.get("reason", ""))[:200]
        for c in cuts:
            # Exact word-bounded span -- see _cut_range_for_segment for why there is no padding
            # and no guard band here. Adjacent cuts are coalesced and snapped to frames later, in
            # _apply_retake_cuts, so the ledger stays a clean per-retake record.
            i = c - 1
            a, b = _cut_range_for_segment(segs, i)
            a = max(a, 0.0)
            b = max(b, a + 0.01)
            ledger.append({
                "id": f"r{i}",  # 0-based seg index -- stable id for this auto-detected cut
                "segIndex": i,
                "text": segs[i].get("text", ""),
                # Explicit CUT RANGE in the checkpoint's timeline -- the single source of truth
                # for applying/restoring, so a cut is no longer tied to whole-segment granularity.
                "cutStartMs": int(a * 1000),
                "cutEndMs": int(b * 1000),
                "startMs": int(float(segs[i]["start"]) * 1000),
                "endMs": int(float(segs[i]["end"]) * 1000),
                "keepText": keep_text,
                "reason": reason,
                "confidence": "high" if votes_for == votes_total else "medium",
            })

    cut_idx -= keep_idx  # never cut a line some other group wants kept (LLM-disagreement safety)
    if not cut_idx:
        return _finish([])
    ledger = [e for e in ledger if (e["segIndex"] + 1) in cut_idx]

    ranges = [(e["cutStartMs"] / 1000.0, e["cutEndMs"] / 1000.0) for e in ledger]
    new_file, new_segs = _apply_retake_cuts(state.working_file, state.working_dir, segs, ranges)
    if new_file == state.working_file:  # cut would have removed the whole clip -- _apply_retake_cuts no-oped
        note("  retake cut would remove the entire clip — skipping.")
        return _finish([])
    state.working_file = new_file
    try:
        from engine import probe_video as _probe_v2
        state.metadata = _probe_v2(new_file)
    except Exception:
        pass
    seg_path.write_text(json.dumps(new_segs, ensure_ascii=False, indent=1))
    state.save()

    return _finish(ledger)


def _pro_export(inp: str, out: str, note) -> dict:
    """Final pro encode: ensure 1080x1920 h264 yuv420p (high@4.1) + faststart, and
    two-pass loudness-normalize audio to -14 LUFS / -1.5 dBTP / LRA 11 (AAC 192k/48k
    stereo). If the input is already h264 1080x1920 yuv420p, the video is stream-copied
    (audio-only re-encode, per spec). Returns the loudnorm measurement dict."""
    v = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,pix_fmt", "-of", "json", inp]))["streams"][0]
    already = (v.get("codec_name") == "h264" and int(v.get("width", 0)) == 1080
               and int(v.get("height", 0)) == 1920 and v.get("pix_fmt") == "yuv420p")
    p1 = subprocess.run(["ffmpeg", "-hide_banner", "-i", inp,
                         "-af", "loudnorm=I=-14:TP=-1.7:LRA=11:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True)
    j = json.loads(p1.stderr[p1.stderr.rfind("{"): p1.stderr.rfind("}") + 1])
    # linear=false (dynamic) engages loudnorm's true-peak limiter; TP target -2.0 (not -1.5)
    # leaves headroom for AAC inter-sample peaks so the FINAL measured true peak stays <= -1.0 dBTP.
    af = (f"loudnorm=I=-14:TP=-1.7:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}"
          f":measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}"
          f":offset={j['target_offset']}:linear=false")
    if already:
        vargs = ["-c:v", "copy"]
        note("  video already h264/1080x1920/yuv420p -> stream-copy (audio-only re-encode).")
    else:
        vargs = ["-vf", "scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
                        "pad=1080:1920:-1:-1:color=black",
                 "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                 "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p"]
        note("  re-encoding video -> 1080x1920 h264 yuv420p (slow/crf18).")
    subprocess.run(["ffmpeg", "-y", "-i", inp, *vargs, "-af", af,
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", out],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return j


def _has_faststart(path: str) -> bool:
    """True if the moov atom is before mdat (i.e. +faststart applied)."""
    with open(path, "rb") as f:
        head = f.read(400000)
    mo, md = head.find(b"moov"), head.find(b"mdat")
    if mo == -1:
        return False           # moov not near the start -> it's at the end
    return md == -1 or mo < md


def _qc(final_path: str, caption_end: float, note) -> dict:
    """QC the final file: resolution, pixfmt, faststart, loudness, true peak, caption sync.
    Prints PASS/FAIL per check and returns a summary dict."""
    v = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt", "-show_entries", "format=duration",
        "-of", "json", final_path]))
    st = v["streams"][0]
    w, h, pix = int(st.get("width", 0)), int(st.get("height", 0)), st.get("pix_fmt", "")
    vdur = float(v["format"].get("duration", 0.0))
    faststart = _has_faststart(final_path)
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", final_path,
                        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    j = json.loads(p.stderr[p.stderr.rfind("{"): p.stderr.rfind("}") + 1])
    lufs, tp = float(j["input_i"]), float(j["input_tp"])
    checks = [
        ("resolution 1080x1920", w == 1080 and h == 1920, f"{w}x{h}"),
        ("pixfmt yuv420p", pix == "yuv420p", pix),
        ("faststart", faststart, "moov first" if faststart else "moov NOT first"),
        ("loudness ~ -14 LUFS (±1)", abs(lufs + 14.0) <= 1.0, f"{lufs:.1f} LUFS"),
        ("true peak <= -1.0 dBTP", tp <= -1.0, f"{tp:.1f} dBTP"),
        ("captions within video", caption_end <= vdur + 0.5, f"caption end {caption_end:.1f}s / dur {vdur:.1f}s"),
    ]
    note("QC:")
    for name, ok, detail in checks:
        note(f"  [{'PASS' if ok else 'FAIL'}] {name} ({detail})")
    return {"checks": [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks],
            "all_pass": all(ok for _, ok, _ in checks),
            "lufs": lufs, "true_peak": tp, "resolution": f"{w}x{h}", "pixfmt": pix, "faststart": faststart}


def run_pipeline(
    video_path: str,
    *,
    clean_audio: bool = False,
    trim_hook: bool = False,
    engine: str = "elevenlabs",
    romanize: bool = True,
    romanize_llm: str = "openai",
    remove_silences: bool = True,
    remove_retakes: bool = True,  # separate from remove_silences -- its own Setup toggle
    color_grade: bool = True,
    color_grade_look: str = "natural",
    color_grade_intensity: float = 0.5,
    captions: bool = True,
    caption_template: str = "word-focus",
    caption_offset: float = -0.2,
    caption_engine: str = "pycaps",  # "pycaps" (default) or "remotion" — NOT the transcription `engine`
    export_1080: bool = True,
    pro_export: bool = True,
    run_qc: bool = True,
    on_project=None,  # optional callback(working_dir): lets a caller learn THIS run's project dir
    on_pregrade=None,  # optional callback(working_file_path): the file right BEFORE color-grade
    # runs (post-trim/clean, pre-grade/upscale) — lets a caller cache this checkpoint so a later
    # "try a different look" action can re-grade without re-transcribing (see regrade() below).
    log=print,
):
    """Run the selected steps. Returns (final_video_path, list_of_log_lines)."""
    import config
    from _project import create_project
    from state import ProjectState
    from engine import probe_video
    from tools import silence, transcript as T, color_grade as CG
    from tools.pycaps_caption import caption_with_pycaps

    logs: list[str] = []

    def note(msg):
        logs.append(msg)
        log(msg)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    # Configure engines/LLM for this run via env (read by the transcribe path).
    os.environ["TRANSCRIBE_ENGINE"] = engine
    os.environ["HINGLISH_ROMANIZE"] = "true" if romanize else "false"
    os.environ["ROMANIZE_LLM"] = romanize_llm

    # Clean working copy (original untouched).
    tag = uuid.uuid4().hex[:6]
    src = OUTDIR / f"src_{tag}.mp4"
    shutil.copy(video_path, src)
    note(f"Loaded clip -> {src.name}")

    config.reload_settings()
    state = create_project(str(src), f"reel-studio-{tag}", config.PROVIDER, config.GEMINI_MODEL)
    if on_project:  # report the project dir so the caller reads ITS OWN transcript (no racy glob-diff)
        try:
            on_project(state.working_dir)
        except Exception:
            pass

    # Silence-trim on the ORIGINAL audio first. Denoising (afftdn) drops the noise
    # floor, which makes the silence detector over-trim natural pauses and can hit a
    # degenerate merge case; cleaning is done AFTER the trim, just before transcription.
    if remove_silences:
        def _dur(p) -> float:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True).stdout.strip()
            try:
                return float(out)
            except ValueError:
                return 0.0

        def _restore(st, path):
            st.working_file = path
            try:
                st.metadata = probe_video(path)
            except Exception:
                pass

        orig_working = state.working_file
        orig_dur = _dur(orig_working)
        # An ABSOLUTE dB threshold is clip-dependent: -33 trims a loud clip nicely but can
        # over-flag a QUIET clip (mean near the threshold) and collapse it to ~nothing. So try
        # -33, and if it kept an implausibly small fraction, fall back to the safer "medium"
        # preset, then (if that ALSO looks implausible) keep the clip untrimmed.
        #
        # The revert floor used to be 45% -- which sounds like a reasonable "sanity" number but
        # is actually way too high: real raw footage with long retakes/false starts/dead air
        # legitimately trims down to 15-30% kept. Verified directly against a real 414s source
        # with heavy retakes: ffmpeg's own silencedetect found 337.8s (81.6%) of true silence,
        # the real trim correctly landed at 84.7s (20.5% kept) -- and the OLD 45% floor discarded
        # that entirely correct trim and shipped the full untrimmed original. A revert should
        # only fire for a genuine calibration failure (e.g. a whisper-quiet recording where the
        # threshold misclassifies actual speech as silence), which collapses to near-nothing, not
        # a legitimately silence-heavy clip. 8% is a floor that only catches real collapse.
        REVERT_FLOOR = 0.08
        note("Trimming silences (moderate — cuts long quiet pauses, keeps breathing room)...")
        # speech_padding_ms=150 (vex's default is 120): how much detected silence is PRESERVED
        # next to speech on each side. Trailing fricatives/plosives ("s", "p") ring out past the
        # point a detector calls silence, so cutting closer than ~0.15s clips the end of the last
        # word -- the single most-reported complaint against every tool in this category, and the
        # one figure a vendor actually publishes a floor for ("less than .15 is not recommended").
        # Costs ~0.03s of retained silence per cut edge; buys not shaving word endings.
        _PAD_MS = 150
        r = silence.execute(
            {"silence_threshold_db": -33.0, "min_silence_duration": 0.4, "trim_edges": True,
             "speech_padding_ms": _PAD_MS}, state)
        state = r.get("updated_state") or state
        new_dur = _dur(state.working_file)
        if orig_dur > 1.0 and new_dur < REVERT_FLOOR * orig_dur:
            note(f"  -33 dB collapsed the clip ({orig_dur:.0f}s -> {new_dur:.1f}s); retrying at medium...")
            _restore(state, orig_working)
            r = silence.execute({"aggressiveness": "medium", "trim_edges": True,
                                 "speech_padding_ms": _PAD_MS}, state)
            state = r.get("updated_state") or state
            new_dur = _dur(state.working_file)
            if orig_dur > 1.0 and new_dur < REVERT_FLOOR * orig_dur:
                note(f"  medium also collapsed ({new_dur:.1f}s); keeping the clip UNTRIMMED.")
                _restore(state, orig_working)
        else:
            note(f"  trimmed {orig_dur:.0f}s -> {new_dur:.1f}s ({100*new_dur/max(orig_dur,0.001):.0f}% kept)")
        state.save()
        note("  " + str(r.get("message", ""))[:90])

    if clean_audio:
        note("Cleaning source audio (before transcription)...")
        cleaned = OUTDIR / f"clean_{tag}.mp4"
        method = _clean_audio(state.working_file, str(cleaned))
        state.working_file = str(cleaned)
        try:
            state.metadata = probe_video(str(cleaned))
        except Exception:
            pass
        state.save()
        note(f"  audio cleaned via {method}.")

    note(f"Transcribing with {engine}" + (" + Hinglish romanize" if romanize else "") + "...")
    r = T.execute({}, state)
    state = r.get("updated_state") or state
    state.save()
    if not r.get("success"):
        note("  transcription FAILED: " + str(r.get("message"))[:120])
        return None, logs
    note("  transcript ready.")

    # Checkpoint saved UNCONDITIONALLY here -- not just inside _cut_retakes(), and not gated on
    # remove_retakes -- so the ready-screen summary and the Fine-tune "Trimmed" sheet can always
    # report how much silence-trim alone removed, even on a run where retake-removal itself is
    # off. Before this, turning the (separate) retakes toggle off silently broke the unrelated
    # silence-trim reporting too, since that reporting piggybacked on a checkpoint only
    # _cut_retakes() used to create.
    try:
        wd_ckpt = pathlib.Path(state.working_dir)
        shutil.copy2(state.working_file, wd_ckpt / "pre_retakes.mp4")
        live_segs = json.loads((wd_ckpt / "transcript.segments.json").read_text())
        (wd_ckpt / "pre_retakes.segments.json").write_text(json.dumps(live_segs, ensure_ascii=False, indent=1))
    except Exception:
        pass

    if remove_retakes:
        # Own toggle, independent of remove_silences -- a retake cuts real spoken content
        # (not dead air), so a user who wants silence trimmed but doesn't trust automatic
        # content-cutting can turn this off on its own. 2-stage: a detector proposes candidate
        # retake groups, an independent judge (biased hard toward rejecting when unsure --
        # leaving an unwanted retake in is a minor inconvenience, cutting real content is not)
        # re-checks each before it can cut anything (see _cut_retakes). Runs AFTER transcription
        # (needs word timestamps) and BEFORE hook-trim, so hook-trim picks the opening line from
        # the already-cleaned content.
        note("Checking for retakes (redone lines)...")
        try:
            n_cut = _cut_retakes(state, note)
            note(f"  cut {n_cut} retake(s)." if n_cut else "  no retakes found.")
        except Exception as e:
            note(f"  retake check skipped ({type(e).__name__}); continuing.")

    if trim_hook:
        note("Trimming to the strongest opening (hook)...")
        try:
            cut = _hook_trim(state, note)
            if cut <= 0:
                note("  already starts on the hook — no trim.")
        except Exception as e:
            note(f"  hook trim skipped ({type(e).__name__}); continuing.")

    # Checkpoint BEFORE grading (unconditionally — even if color_grade is off this run, capturing
    # it still lets a later Fine-tune "apply a look" action work without a re-ingest).
    if on_pregrade:
        try:
            on_pregrade(state.working_file)
        except Exception:
            pass

    if color_grade:
        note(f"Applying {color_grade_look} color grade (intensity {color_grade_intensity:.2f})...")
        if color_grade_look in _DIRECT_LOOKS:
            # Same WYSIWYG direct-ffmpeg path as the Fine-tune "Look" picker's regrade() —
            # keeps the Setup-screen toggle and the later re-apply visually consistent instead
            # of the initial grade going through the weaker shot-aware auto grader below.
            working = state.working_file
            graded = str(pathlib.Path(working).with_name(pathlib.Path(working).stem + "_grade.mp4"))
            vf = _look_ffmpeg_vf(color_grade_look, color_grade_intensity)
            subprocess.run([
                "ffmpeg", "-y", "-i", working, "-vf", vf,
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k", graded,
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            state.working_file = graded
            state.save()
            note(f"  applied direct {color_grade_look} grade.")
        else:
            try:
                r = CG.execute({"look": color_grade_look, "intensity": color_grade_intensity}, state)
            except Exception:
                r = CG.execute({}, state)
            state = r.get("updated_state") or state
            state.save()
            note("  " + str(r.get("message", ""))[:90])

    working = state.working_file
    wd = pathlib.Path(state.working_dir)

    final = OUTDIR / f"reel_{tag}.mp4"
    render_out = OUTDIR / f"reel_raw_{tag}.mp4"   # pre pro-export
    caption_input = working
    caption_end = 0.0

    if export_1080:
        note("Upscaling to 1080x1920 (crisp captions)...")
        up = OUTDIR / f"graded_1080_{tag}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", working,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", str(up),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        caption_input = str(up)

    if captions:
        # Shared for BOTH caption engines so downstream QC is identical.
        wj_path = OUTDIR / f"transcript_{tag}.whisper.json"
        segs = json.loads((wd / "transcript.segments.json").read_text())
        nwords = _build_whisper_json(segs, wj_path)
        caption_end = max((float(w["end"]) for s in segs for w in s.get("words", [])), default=0.0)
        if caption_engine == "remotion":
            note("Rendering Remotion captions (word-focus, 60fps overlay)...")
            # render_captions.py lives alongside this file (not on sys.path by default).
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
            from render_captions import render_captions
            render_captions(caption_input, str(wj_path), str(render_out),
                            bottom_percent=22, fps=60)
            note(f"  Remotion captions overlaid ({nwords} words).")
        else:
            note(f"Rendering pycaps captions ({caption_template})...")
            caption_with_pycaps(caption_input, str(wj_path), str(render_out),
                                template=caption_template, layout_align="bottom",
                                layout_offset=caption_offset, quality="high")
            note(f"  captions burned ({nwords} words).")
    else:
        shutil.copy(caption_input, render_out)

    if pro_export:
        note("Pro export + loudness normalize (-14 LUFS / -1.5 dBTP, faststart)...")
        meas = _pro_export(str(render_out), str(final), note)
        note(f"  loudnorm measured input {float(meas['input_i']):.1f} LUFS / TP {float(meas['input_tp']):.1f} dBTP "
             f"-> normalized to -14 / -1.5.")
    else:
        shutil.copy(render_out, final)

    if run_qc:
        _qc(str(final), caption_end, note)

    note(f"DONE -> {final}")
    return str(final), logs


# CSS live-preview look definitions, mirrored verbatim from the mobile frontend's
# LOOK_FILTER_BASE (reel-studio-ui/src/mobile/MobileApp.tsx). The picker shows the user THIS
# as a live CSS filter while they choose; the direct-look render path below reproduces the
# SAME transform server-side so the exported reel matches what they previewed (WYSIWYG).
_LOOK_FILTER_BASE = {
    "vibrant": {"saturate": 1.6, "contrast": 1.15, "brightness": 1.05},
    "cinematic": {"contrast": 1.2, "saturate": 0.82, "brightness": 0.95, "sepia": 0.15, "hue": -8},
    "warm": {"sepia": 0.25, "saturate": 1.35, "hue": -8, "brightness": 1.05, "contrast": 1.05},
    "cool": {"saturate": 1.1, "contrast": 1.1, "hue": 10, "grayscale": 0.04},
    "documentary": {"grayscale": 0.18, "contrast": 1.08, "saturate": 0.85, "brightness": 0.98},
    "punchy": {"contrast": 1.35, "saturate": 1.55, "brightness": 1.06, "sepia": 0.05},
}
# Explicit, user-chosen stylized looks bypass the shot-aware AUTO grader entirely. That engine
# is a "protect the subject" auto-corrector -- it hard-caps saturation/contrast and kills WB
# shifts on face-heavy footage, so a user who explicitly picks "vibrant"/"warm" saw almost no
# change (the whole point of picking a look). For these, apply the look directly, matching the
# preview. `auto`/`natural` still go through the shot-aware engine (subtle, per-scene, correct).
_DIRECT_LOOKS = set(_LOOK_FILTER_BASE)


def _mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _css_op_matrices(look: str, t: float):
    """Build each CSS color-filter as a (3x3 matrix, offset-vector) linear op, in the SAME
    order and scaling the frontend's cssFilterFor() applies them (contrast -> brightness ->
    saturate -> sepia -> grayscale -> hue-rotate). Every one of these CSS filters is a linear
    color transform, so the whole chain composes into a single matrix + offset -- which is
    what makes an EXACT ffmpeg reproduction possible (see _look_ffmpeg_vf)."""
    import math
    b = _LOOK_FILTER_BASE[look]
    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    ops = []  # each: (matrix, offset)

    c = 1.0 + (b.get("contrast", 1.0) - 1.0) * t
    ops.append(([[c, 0, 0], [0, c, 0], [0, 0, c]], [0.5 * (1 - c)] * 3))

    br = 1.0 + (b.get("brightness", 1.0) - 1.0) * t
    ops.append(([[br, 0, 0], [0, br, 0], [0, 0, br]], [0.0, 0.0, 0.0]))

    s = 1.0 + (b.get("saturate", 1.0) - 1.0) * t
    lr, lg, lb = 0.2126, 0.7152, 0.0722
    ops.append(([[lr + s * (1 - lr), lg - s * lg, lb - s * lb],
                 [lr - s * lr, lg + s * (1 - lg), lb - s * lb],
                 [lr - s * lr, lg - s * lg, lb + s * (1 - lb)]], [0.0, 0.0, 0.0]))

    sep = b.get("sepia", 0.0) * t
    if sep > 0.0:
        sm = [[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]]
        ops.append(([[ident[i][j] * (1 - sep) + sm[i][j] * sep for j in range(3)] for i in range(3)], [0.0] * 3))

    gray = b.get("grayscale", 0.0) * t
    if gray > 0.0:
        gm = [[lr, lg, lb]] * 3
        ops.append(([[ident[i][j] * (1 - gray) + gm[i][j] * gray for j in range(3)] for i in range(3)], [0.0] * 3))

    deg = b.get("hue", 0.0) * t
    if abs(deg) > 0.001:
        a = math.radians(deg)
        cos, sin = math.cos(a), math.sin(a)
        ops.append(([[0.213 + cos * 0.787 - sin * 0.213, 0.715 - cos * 0.715 - sin * 0.715, 0.072 - cos * 0.072 + sin * 0.928],
                     [0.213 - cos * 0.213 + sin * 0.143, 0.715 + cos * 0.285 + sin * 0.140, 0.072 - cos * 0.072 - sin * 0.283],
                     [0.213 - cos * 0.213 - sin * 0.787, 0.715 - cos * 0.715 + sin * 0.715, 0.072 + cos * 0.928 + sin * 0.072]], [0.0] * 3))
    return ops


def _look_ffmpeg_vf(look: str, intensity: float) -> str:
    """Reproduce the frontend's CSS-preview look (LOOK_FILTER_BASE / cssFilterFor) EXACTLY in
    ffmpeg. Earlier this approximated the look with ffmpeg's eq filter using the same numbers
    as the CSS preview -- but CSS saturate/contrast and ffmpeg eq use different math, so the
    render came out visibly WEAKER than the preview (measured: preview +46% saturation vs
    render +26% for vibrant@100%), which read as "the color barely applied." Instead, compose
    the whole CSS filter chain into one exact color matrix + offset and apply it with ffmpeg's
    colorchannelmixer, so what the user previews is what the render produces (verified to match
    the CSS preview's saturation within 1%)."""
    t = max(0.0, min(float(intensity), 1.5))
    M = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    off = [0.0, 0.0, 0.0]
    for Mi, ci in _css_op_matrices(look, t):
        M = _mat_mul(Mi, M)
        off = [sum(Mi[i][k] * off[k] for k in range(3)) + ci[i] for i in range(3)]
    # colorchannelmixer applies the 3x3; the composed offset is near-uniform (contrast's
    # pedestal carried through luminance-preserving matrices), so a single eq brightness add
    # of its mean reproduces it to <1/255 -- imperceptible. ffmpeg clamps each coefficient to
    # [-2, 2] (values outside error out with exit 222 -- hit by strong looks like punchy at
    # full intensity), so clamp here; the tiny clip at the extreme is visually negligible.
    def _cl(v):
        return max(-2.0, min(2.0, v))
    m = M
    ccm = (f"colorchannelmixer="
           f"{_cl(m[0][0]):.5f}:{_cl(m[0][1]):.5f}:{_cl(m[0][2]):.5f}:0:"
           f"{_cl(m[1][0]):.5f}:{_cl(m[1][1]):.5f}:{_cl(m[1][2]):.5f}:0:"
           f"{_cl(m[2][0]):.5f}:{_cl(m[2][1]):.5f}:{_cl(m[2][2]):.5f}:0")
    parts = [ccm]
    mean_off = round(sum(off) / 3.0, 5)
    if abs(mean_off) > 0.0005:
        parts.append(f"eq=brightness={mean_off}")
    parts.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black")
    return ",".join(parts)


def restore_retakes(
    working_dir: str,
    output_path: str,
    *,
    keep_ids: list[str] | None = None,
    log=print,
) -> str:
    """Re-applies the retake-cut ledger captured by `_cut_retakes` against its `pre_retakes.mp4`
    checkpoint, EXCLUDING `keep_ids` (ledger entries the user wants put back). Used by the
    mobile app's Fine-tune "Trimmed" sheet so undoing one auto-cut retake is a quick re-bake
    from the checkpoint, not a full re-ingest -- same idea as `regrade()` below, one checkpoint
    earlier in the pipeline. `keep_ids` is the FULL current set of restored ids (not a delta),
    matching how every other Fine-tune toggle sends its complete current state on every call."""
    logs: list[str] = []

    def note(msg):
        logs.append(msg)
        log(msg)

    wd = pathlib.Path(working_dir)
    pre_video = wd / "pre_retakes.mp4"
    pre_segs_path = wd / "pre_retakes.segments.json"
    ledger_path = wd / "retake_cuts.json"
    if not (pre_video.exists() and pre_segs_path.exists() and ledger_path.exists()):
        raise FileNotFoundError("no retake checkpoint for this project (nothing was auto-cut)")

    segs = json.loads(pre_segs_path.read_text())
    ledger = json.loads(ledger_path.read_text())
    keep = set(keep_ids or [])
    active = [e for e in ledger if isinstance(e, dict) and e.get("id") not in keep]
    ranges = [(float(e["cutStartMs"]) / 1000.0, float(e["cutEndMs"]) / 1000.0)
              for e in active if e.get("cutStartMs") is not None and e.get("cutEndMs") is not None]

    note(f"Restoring {len(keep)} retake(s), re-applying {len(ranges)} cut(s)...")
    new_file, new_segs = _apply_retake_cuts(str(pre_video), working_dir, segs, ranges)

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(new_file, out)
    # Keep the LIVE transcript in sync so captions match the restored timeline -- this project's
    # transcript.segments.json is what every downstream render (captions/broll/etc.) reads.
    (wd / "transcript.segments.json").write_text(json.dumps(new_segs, ensure_ascii=False, indent=1))
    note(f"DONE -> {out}")
    return str(out)


def regrade(
    pregrade_video: str,
    output_path: str,
    *,
    look: str = "natural",
    intensity: float = 0.5,
    export_1080: bool = True,
    log=print,
) -> str:
    """Re-run ONLY color-grade (+ upscale) against a cached PRE-grade checkpoint captured by
    run_pipeline's `on_pregrade` callback — used by the mobile app's Fine-tune "Look" picker so
    trying a different look after the first render is a quick re-bake, not a full re-ingest
    (transcription/romanization/silence-trim are all unaffected by color choice and would
    otherwise be wastefully repeated)."""
    logs: list[str] = []

    def note(msg):
        logs.append(msg)
        log(msg)

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if look in _DIRECT_LOOKS:
        # Direct, WYSIWYG look: apply the previewed filter in one ffmpeg pass, no auto grader.
        note(f"Applying {look} look (intensity {intensity:.2f})...")
        vf = _look_ffmpeg_vf(look, intensity)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(pregrade_video), "-vf", vf,
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", str(out),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        note(f"DONE -> {out}")
        return str(out)

    # auto / natural: the shot-aware auto grader is the right tool (subtle, per-scene).
    import config
    from _project import create_project
    from tools import color_grade as CG

    config.reload_settings()
    tag = uuid.uuid4().hex[:6]
    state = create_project(pregrade_video, f"regrade-{tag}", config.PROVIDER, config.GEMINI_MODEL)

    note(f"Applying {look} color grade (intensity {intensity:.2f})...")
    try:
        r = CG.execute({"look": look, "intensity": intensity}, state)
    except Exception:
        r = CG.execute({}, state)
    state = r.get("updated_state") or state
    state.save()
    note("  " + str(r.get("message", ""))[:90])

    working = state.working_file
    if export_1080:
        note("Upscaling to 1080x1920 (crisp captions)...")
        subprocess.run([
            "ffmpeg", "-y", "-i", working,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", str(out),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        shutil.copy(working, out)
    note(f"DONE -> {out}")
    return str(out)


def render_look_previews(
    video_path: str,
    timestamp_s: float,
    looks: list[str],
    *,
    intensity: float = 0.5,
    max_dimension: int = 220,
) -> dict:
    """Render a small preview thumbnail of `video_path` at `timestamp_s`, once per look in
    `looks`, on the FAST (non-shot-aware) grading path — cheap enough to run 8x for an
    interactive picker, unlike the real shot-aware grade regrade()/run_pipeline apply. Returns
    {look: base64_png_str} (a look maps to None if that one look's render failed, so one bad
    look doesn't take down the whole picker — the frontend falls back to a plain color swatch
    for that tile). "auto" is approximated via its baseline profile here — the real shot-aware
    per-scene adaptation only happens when it's actually applied, not in this cheap preview."""
    import base64
    import io

    from PIL import Image

    from color_grading import build_color_grade_plan, render_color_grade_preview_frames
    from engine import probe_video

    metadata = probe_video(video_path)
    out: dict = {}
    for look in looks:
        try:
            plan = build_color_grade_plan(video_path, metadata, look=look, intensity=intensity, sample_count=3)
            frames = render_color_grade_preview_frames(
                video_path, metadata, [timestamp_s], plan.filter_graph, max_dimension=max_dimension,
            )
            img = Image.fromarray(frames[0], mode="RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out[look] = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            out[look] = None
    return out


def recaption_only(
    caption_base_video: str,
    transcript_json_path: str,
    output_path: str,
    *,
    caption_engine: str = "remotion",
    bottom_percent: float = 22,
    style: str = "word-focus",
    fps: int = 60,
    run_qc: bool = True,
    log=print,
) -> dict:
    """FAST captions-only path: (re)burn captions onto an ALREADY graded+upscaled
    pre-caption video, then pro-export + loudnorm + QC. REUSES run_pipeline's helpers
    (_pro_export, _qc) and the same caption engines — it does NOT transcribe / trim / grade.

    Emits NDJSON progress via log(...): {"event":"step",...} per sub-step, {"event":"log",...}
    for helper output, and a final {"event":"done","output":...,"qc":[...]}.
    Returns {"output", "qc"}.
    """
    from tools.pycaps_caption import caption_with_pycaps

    def _emit(obj: dict) -> None:
        log(json.dumps(obj, ensure_ascii=False))

    def _note(msg) -> None:
        _emit({"event": "log", "text": str(msg)})

    OUTDIR.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:6]
    render_out = OUTDIR / f"recap_raw_{tag}.mp4"
    final = pathlib.Path(output_path)
    final.parent.mkdir(parents=True, exist_ok=True)

    wj = json.loads(pathlib.Path(transcript_json_path).read_text())
    segs = wj.get("segments", []) if isinstance(wj, dict) else wj
    caption_end = max(
        (float(w.get("end", 0.0)) for s in segs for w in s.get("words", [])), default=0.0
    )

    # 1) captions (same engines as run_pipeline's caption step)
    _emit({"event": "step", "name": "Captions", "status": "running", "engine": caption_engine})
    if caption_engine == "remotion":
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from render_captions import render_captions
        render_captions(str(caption_base_video), str(transcript_json_path), str(render_out),
                        bottom_percent=bottom_percent, style=style, fps=fps, log=_note)
        _note(f"Remotion captions overlaid (engine={caption_engine}).")
    else:
        layout_offset = -(float(bottom_percent) / 100.0)  # bottom_percent% up -> pycaps fraction
        caption_with_pycaps(str(caption_base_video), str(transcript_json_path), str(render_out),
                            template=style, layout_align="bottom", layout_offset=layout_offset,
                            quality="high")
        _note(f"pycaps captions burned (offset={layout_offset:.2f}).")
    _emit({"event": "step", "name": "Captions", "status": "done"})

    # 2) pro-export + 2-pass loudnorm (the SAME helper run_pipeline uses)
    _emit({"event": "step", "name": "Export", "status": "running"})
    _pro_export(str(render_out), str(final), _note)
    _emit({"event": "step", "name": "Export", "status": "done"})

    # 3) QC (the SAME helper)
    qc_checks: list[dict] = []
    if run_qc:
        _emit({"event": "step", "name": "QC", "status": "running"})
        qc = _qc(str(final), caption_end, _note)
        qc_checks = [
            {"label": c["name"], "pass": c["pass"], "detail": c["detail"]} for c in qc["checks"]
        ]
        _emit({"event": "step", "name": "QC", "status": "done", "all_pass": qc["all_pass"]})

    try:
        render_out.unlink()
    except OSError:
        pass

    _emit({"event": "done", "output": str(final), "qc": qc_checks})
    return {"output": str(final), "qc": qc_checks}
