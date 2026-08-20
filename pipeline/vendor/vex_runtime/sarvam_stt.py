"""Sarvam AI speech-to-text for Vex.

Transcribes audio via Sarvam's Saaras STT model and returns a result dict in the
SAME shape Vex's Whisper path returns, so the rest of the transcription pipeline
(_normalize_whisper_segments -> transcript.srt / .words.json / .segments.json)
works unchanged.

Key facts (Sarvam /speech-to-text):
  - endpoint POST https://api.sarvam.ai/speech-to-text
  - auth header: api-subscription-key: <key>
  - form: model=saaras:v3, mode=transcribe, language_code=hi-IN, with_timestamps=true
  - REST limit ~30s per request -> we chunk audio at 28s and offset timestamps
  - response: {"transcript": str, "language_code": str,
               "timestamps": {"words": [...], "start_time_seconds": [...],
                              "end_time_seconds": [...]}}   (only if with_timestamps=true)
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

SARVAM_URL = "https://api.sarvam.ai/speech-to-text"
CHUNK_SECONDS = 28.0            # under Sarvam's ~30s REST limit
SEG_PAUSE = 0.55               # start a new caption segment when the gap between words >= this
SEG_MAX_WORDS = 9              # and cap segment length so SRT lines stay short


def _ffmpeg_extract(src: Path, dst: Path, ss: float | None = None, dur: float | None = None) -> None:
    cmd = ["ffmpeg", "-y"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    )
    try:
        return float(out.decode().strip())
    except ValueError:
        return 0.0


def _segment(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group word dicts into caption segments on pauses / sentence punctuation / length."""
    segments: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    prev_end: float | None = None
    for w in words:
        gap = (w["start"] - prev_end) if prev_end is not None else 0.0
        ends_sentence = bool(cur) and str(cur[-1]["word"]).rstrip()[-1:] in "।.?!"
        if cur and (gap >= SEG_PAUSE or ends_sentence or len(cur) >= SEG_MAX_WORDS):
            segments.append(_mk_segment(cur))
            cur = []
        cur.append(w)
        prev_end = w["end"]
    if cur:
        segments.append(_mk_segment(cur))
    return segments


def _mk_segment(words: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(str(w["word"]).strip() for w in words).strip()
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}


def _words_from_response(result: dict, transcript: str, offset: float,
                         chunk_dur: float) -> tuple[list[dict[str, Any]], bool]:
    """Build offset word dicts from Sarvam's response.

    Returns (words, had_real_word_timestamps). Falls back to even-distributed
    timing across the chunk when Sarvam did not return per-word timestamps.
    """
    ts = result.get("timestamps") or {}
    ws = ts.get("words") or []
    starts = ts.get("start_time_seconds") or []
    ends = ts.get("end_time_seconds") or []
    if ws and len(ws) == len(starts) == len(ends):
        words = []
        for token, s, e in zip(ws, starts, ends):
            if token is None or s is None or e is None:
                continue
            words.append({"word": str(token), "start": float(s) + offset,
                          "end": float(e) + offset})
        if words:
            return words, True
    # Fallback: no usable word timestamps — split transcript into tokens and
    # distribute time evenly across the chunk (so downstream still works).
    tokens = [t for t in transcript.split() if t]
    if not tokens:
        return [], False
    step = chunk_dur / len(tokens)
    words = [{"word": tok, "start": offset + i * step, "end": offset + (i + 1) * step}
             for i, tok in enumerate(tokens)]
    return words, False


def transcribe_with_sarvam(
    media_path: str | Path,
    *,
    api_key: str,
    language_code: str = "hi-IN",
    model: str = "saaras:v3",
    mode: str = "transcribe",
    chunk_seconds: float = CHUNK_SECONDS,
    verbose: bool = False,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Sarvam API key is missing (set SARVAM_API_KEY in .env).")
    import requests  # lazy

    src = Path(media_path)
    headers = {"api-subscription-key": api_key}
    all_words: list[dict[str, Any]] = []
    real_ts_any = False
    real_ts_all = True

    with tempfile.TemporaryDirectory(prefix="sarvam-") as td:
        tdp = Path(td)
        full_wav = tdp / "full.wav"
        _ffmpeg_extract(src, full_wav)
        total = _duration(full_wav)
        n_chunks = max(1, int((total + chunk_seconds - 1) // chunk_seconds))
        if verbose:
            print(f"  sarvam: {total:.1f}s audio -> {n_chunks} chunk(s) of <= {chunk_seconds:.0f}s")

        for i in range(n_chunks):
            offset = i * chunk_seconds
            this_dur = min(chunk_seconds, max(0.0, total - offset))
            if this_dur <= 0.05:
                continue
            chunk_wav = tdp / f"chunk_{i}.wav"
            _ffmpeg_extract(full_wav, chunk_wav, ss=offset, dur=chunk_seconds)
            with open(chunk_wav, "rb") as fh:
                files = {"file": (f"chunk_{i}.wav", fh, "audio/wav")}
                data = {"model": model, "mode": mode, "language_code": language_code,
                        "with_timestamps": "true"}
                resp = requests.post(SARVAM_URL, headers=headers, files=files, data=data, timeout=180)
            if resp.status_code == 401:
                raise RuntimeError("Sarvam API key rejected (401). Check SARVAM_API_KEY.")
            if resp.status_code == 429:
                raise RuntimeError("Sarvam rate limit (429). Try again shortly.")
            resp.raise_for_status()
            result = resp.json()
            transcript = (result.get("transcript") or "").strip()
            if not transcript:
                continue
            words, had_ts = _words_from_response(result, transcript, offset, this_dur)
            real_ts_any = real_ts_any or had_ts
            real_ts_all = real_ts_all and had_ts
            all_words.extend(words)
            if verbose:
                mark = "word-ts" if had_ts else "synth-ts"
                print(f"    chunk {i} @ {offset:.0f}s [{mark}]: {transcript[:60]}")

    segments = _segment(all_words)
    full_text = " ".join(s["text"] for s in segments).strip()
    return {
        "text": full_text,
        "language": (language_code.split("-")[0] if "-" in language_code else language_code),
        "segments": segments,
        # informational (ignored by _normalize_whisper_segments):
        "_engine": "sarvam",
        "_word_timestamps": bool(real_ts_any),
        "_word_timestamps_all_chunks": bool(real_ts_all and all_words),
    }
