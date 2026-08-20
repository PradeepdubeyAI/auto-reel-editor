#!/usr/bin/env python3
"""zoom_plan.py — auto-zoom moment selection from TWO agreeing signals (run with the VEX venv).

Reads a JSON payload (argv[1]) = {"words":[{text,startMs,endMs}...], "durationMs":int,
"baseVideo": "<path>"} and returns emphasis-based punch-in moments computed from BOTH:

  (a) LLM (meaning)   — gpt-4o-mini reads the transcript and proposes emphasis spans.
  (b) AUDIO (delivery)— ffmpeg decodes mono PCM, numpy computes an RMS/dB envelope and picks
                        LOCAL PEAKS (louder + preceded by a brief pause = vocal emphasis).
                        ffmpeg + numpy ONLY (no torch).

MERGE: an LLM span that contains an audio peak becomes source "both" — snap a QUICK PUNCH to the
peak (lands on the stressed word). LLM-only spans stay gentle SLOW PUSHES. Strong audio peaks with
no LLM span become gentle "audio" pushes. The result is spacing/coverage/hook-close clamped.

Craft rules (subtle 1.08-1.25x, ease in/out, ~1 per 8-12s, protect hook/close, one-per-moment,
vary the scale) are enforced deterministically here + prompted to the LLM. Never returns the API key.

Usage:  <vex-venv-python> zoom_plan.py <payload.json>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

# --- craft constants ---
HOOK_PROTECT_MS = 3500       # no zoom overlaps the hook...
CLOSE_PROTECT_MS = 3500      # ...or the close (speaker owns the extremes)
MIN_SPAN_MS = 1200
MAX_SPAN_MS = 4000
MIN_GAP_MS = 8000            # cadence ~1 per 8-12s
MAX_COVERAGE = 0.50
# Scale bands, sized against the researched rhetorical-weight punch-in ladder (Normal 1.0x /
# Emphasis ~1.25x / Critical ~1.4-1.6x for a 9:16 crop -- see reel-editing-playbook references/
# 04-zoom.md). The old bands (slow 1.10-1.14, punch 1.16-1.22) topped out below even the
# "Emphasis" tier, which read as barely-there on a phone screen -- this is why zooms felt "very
# less." slow_push now sits at the Emphasis tier; quick_punch (an LLM+audio-agreed peak, the
# strongest signal we have) reaches into the Critical tier.
SCALE_SLOW = [1.20, 1.24, 1.28, 1.22, 1.26]   # slow push on a key line / topic shift
SCALE_PUNCH = [1.35, 1.42, 1.30, 1.48, 1.38]  # quick punch on an agreed peak (stronger)
SCALE_GENTLE = 1.14                            # audio-only / extreme-adjacent
VALID_STYLES = {"slow_push", "quick_punch"}
VALID_REASONS = {"key_line", "punchline", "key_number", "topic_shift"}


def _ms(x, d=0):
    try:
        return int(round(float(x)))
    except (TypeError, ValueError):
        return d


def build_timed_transcript(words: list[dict]) -> str:
    lines, cur, cur_start = [], [], None
    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        if cur_start is None:
            cur_start = _ms(w.get("startMs"))
        cur.append(text)
        end = _ms(w.get("endMs"))
        if end - cur_start >= 3500 or len(cur) >= 14:
            lines.append(f"[{cur_start/1000:6.2f}s] {' '.join(cur)}")
            cur, cur_start = [], None
    if cur and cur_start is not None:
        lines.append(f"[{cur_start/1000:6.2f}s] {' '.join(cur)}")
    return "\n".join(lines)


def phrase_in_span(words: list[dict], a: int, b: int) -> str:
    hits = [str(w.get("text", "")).strip() for w in words
            if _ms(w.get("startMs")) < b and _ms(w.get("endMs")) > a]
    return " ".join(t for t in hits if t)[:120]


# ----------------------------------------------------------------- LLM signal --
SYSTEM_PROMPT = f"""You direct SUBTLE emphasis zooms for a Hindi/Hinglish talking-head vertical reel.
A zoom is a gentle push-in that marks a MEANINGFUL moment. Given a timestamped transcript, return
ONLY JSON: {{"zooms":[ ... ]}} where each zoom has:
  "spanStartMs" (int), "spanEndMs" (int)
  "reason": "key_line" | "punchline" | "key_number" | "topic_shift"
  "suggestedStyle": "slow_push" (default) | "quick_punch" (for a punchline / stressed beat)
  "transcriptPhrase": the words spoken during the span
RULES:
  - Zoom = EMPHASIS only. Never on a timer. Prefer key lines, punchlines, key numbers, topic shifts.
  - Cadence: about ONE zoom per 8-12 seconds. FEWER, stronger moments beat many.
  - Each span {MIN_SPAN_MS}-{MAX_SPAN_MS} ms.
  - PROTECT the hook (first {HOOK_PROTECT_MS} ms) and close (last {CLOSE_PROTECT_MS} ms): no zoom there.
  - Understated, educational tone — not frantic."""


def llm_spans(words: list[dict], duration_ms: int) -> tuple[list[dict], str | None]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return [], "OPENAI_API_KEY not set"
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    import requests

    user = (f"Runtime: {duration_ms} ms. Hook 0-{HOOK_PROTECT_MS} ms and close "
            f"{max(0, duration_ms-CLOSE_PROTECT_MS)}-{duration_ms} ms are OFF-LIMITS.\n\n"
            f"Transcript:\n{build_timed_transcript(words)}")
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.4, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        return (data.get("zooms") or []), None
    except Exception as e:  # noqa: BLE001 — safe message, never the key
        return [], str(e)[:160]


# --------------------------------------------------------------- audio signal --
def audio_peaks(base_video: str) -> list[int]:
    """RMS/dB envelope local peaks that are LOUDER and PRECEDED BY A DIP (a pause) — the
    Submagic-style 'louder volume / pause then emphasis' cue. ffmpeg + numpy only."""
    import numpy as np

    sr = 16000
    win = int(0.05 * sr)  # 50 ms frames
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", base_video, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout
    except Exception:
        return []
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size < win * 4:
        return []
    n = x.size // win
    frames = x[: n * win].reshape(n, win)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
    db = 20.0 * np.log10(rms + 1e-6)
    # smooth ~150 ms
    k = 3
    sm = np.convolve(db, np.ones(k) / k, mode="same")
    thr = sm.mean() + 0.4 * sm.std()
    peaks: list[int] = []
    for i in range(3, len(sm) - 3):
        if sm[i] < thr:
            continue
        if sm[i] < sm[i - 3:i + 4].max():  # not a local max
            continue
        pre = sm[max(0, i - 10):i]         # ~500 ms before
        if pre.size and (sm[i] - pre.min()) >= 3.0:  # rose >=3 dB out of a dip
            ms = int(i * win / sr * 1000)
            if not peaks or ms - peaks[-1] >= 1200:   # de-dup within 1.2 s
                peaks.append(ms)
    return peaks


# --------------------------------------------------------------------- merge --
def _clamp_span(a: int, b: int) -> tuple[int, int] | None:
    if b <= a:
        return None
    b = min(b, a + MAX_SPAN_MS)
    if b - a < MIN_SPAN_MS:
        b = a + MIN_SPAN_MS
    return a, b


def merge_signals(raw_llm: list, peaks: list[int], words: list[dict], duration_ms: int) -> list[dict]:
    """Tunable merge: LLM spans + audio peaks -> de-conflicted, spacing/coverage-clamped zooms with
    a `source` ('both'|'llm'|'audio'). Kept behind one function so scoring can change without the UI."""
    close_start = max(0, duration_ms - CLOSE_PROTECT_MS)
    peaks = sorted(peaks)
    used_peaks: set[int] = set()
    cands: list[dict] = []

    # (1) LLM spans, promoted to "both" (quick punch on the contained peak) where a peak agrees
    for m in raw_llm if isinstance(raw_llm, list) else []:
        if not isinstance(m, dict):
            continue
        cl = _clamp_span(_ms(m.get("spanStartMs")), _ms(m.get("spanEndMs")))
        if cl is None:
            continue
        a, b = cl
        if a < HOOK_PROTECT_MS or b > close_start:
            continue  # protect extremes
        reason = str(m.get("reason", "key_line")).strip().lower()
        if reason not in VALID_REASONS:
            reason = "key_line"
        style = str(m.get("suggestedStyle", "slow_push")).strip().lower()
        if style not in VALID_STYLES:
            style = "slow_push"
        peak = next((p for p in peaks if a <= p <= b and p not in used_peaks), None)
        if peak is not None:
            used_peaks.add(peak)
            source, style = "both", "quick_punch"          # agreement -> snappier
        else:
            source = "llm"
        cands.append({"spanStartMs": a, "spanEndMs": b, "reason": reason, "style": style,
                      "source": source, "priority": 2 if source == "both" else 1,
                      "transcriptPhrase": str(m.get("transcriptPhrase", "")).strip() or phrase_in_span(words, a, b),
                      "peakMs": peak})

    # (2) strong audio peaks with no LLM span -> gentle "audio" pushes
    for p in peaks:
        if p in used_peaks or p < HOOK_PROTECT_MS or p > close_start:
            continue
        a = max(HOOK_PROTECT_MS, p - 600)
        b = min(close_start, a + 2200)
        cl = _clamp_span(a, b)
        if cl is None:
            continue
        a, b = cl
        cands.append({"spanStartMs": a, "spanEndMs": b, "reason": "key_line", "style": "slow_push",
                      "source": "audio", "priority": 0,
                      "transcriptPhrase": phrase_in_span(words, a, b), "peakMs": p})

    # (3) de-conflict: spacing (keep higher priority), coverage cap; assign varied scales
    cands.sort(key=lambda z: (z["spanStartMs"], -z["priority"]))
    kept: list[dict] = []
    covered = 0
    budget = MAX_COVERAGE * max(duration_ms, 1)
    for z in cands:
        if kept and z["spanStartMs"] - kept[-1]["spanStartMs"] < MIN_GAP_MS:
            # too close: replace the previous if this one has higher priority
            if z["priority"] > kept[-1]["priority"]:
                covered -= kept[-1]["spanEndMs"] - kept[-1]["spanStartMs"]
                kept.pop()
            else:
                continue
        dur = z["spanEndMs"] - z["spanStartMs"]
        if covered + dur > budget:
            continue
        kept.append(z)
        covered += dur

    out: list[dict] = []
    slow_i = punch_i = 0
    for z in kept:
        if z["source"] == "audio":
            scale = SCALE_GENTLE
        elif z["style"] == "quick_punch":
            scale = SCALE_PUNCH[punch_i % len(SCALE_PUNCH)]
            punch_i += 1
        else:
            scale = SCALE_SLOW[slow_i % len(SCALE_SLOW)]
            slow_i += 1
        out.append({
            "spanStartMs": z["spanStartMs"], "spanEndMs": z["spanEndMs"],
            "style": z["style"], "targetScale": round(scale, 3), "source": z["source"],
            "reason": z["reason"], "transcriptPhrase": z["transcriptPhrase"],
        })
    return out


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    words = payload.get("words") or []
    duration_ms = _ms(payload.get("durationMs")) or (_ms(words[-1].get("endMs")) + 2000 if words else 0)
    base_video = str(payload.get("baseVideo") or "")

    raw_llm, llm_err = llm_spans(words, duration_ms)
    peaks = audio_peaks(base_video) if base_video and Path(base_video).exists() else []
    zooms = merge_signals(raw_llm, peaks, words, duration_ms)

    by_src = {"llm": 0, "audio": 0, "both": 0}
    for z in zooms:
        by_src[z["source"]] = by_src.get(z["source"], 0) + 1
    result = {
        "zooms": zooms,
        "meta": {
            "durationMs": duration_ms,
            "llmSpanCount": len(raw_llm),
            "audioPeakCount": len(peaks),
            "audioPeaksMs": peaks[:40],
            "bySource": by_src,
            "cadenceSecPerZoom": round((duration_ms / 1000) / max(len(zooms), 1), 1),
            "hookProtectMs": HOOK_PROTECT_MS,
            "closeProtectMs": CLOSE_PROTECT_MS,
        },
    }
    if not zooms and llm_err and not peaks:
        result["error"] = f"zoom plan failed: {llm_err}"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
