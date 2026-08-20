"""ElevenLabs Scribe speech-to-text for Vex.

Scribe (model scribe_v1) returns TRUE per-word timestamps and transcribes Hindi
accurately, with no 30-second request limit. We use it as the word-timed
transcription source so downstream karaoke/word-by-word captions have real
per-word timing. Returns the SAME dict shape as Vex's Whisper path so
_normalize_whisper_segments (transcript.srt / .words.json / .segments.json)
works unchanged.

Endpoint: POST https://api.elevenlabs.io/v1/speech-to-text
Auth:     xi-api-key: <key>
Form:     model_id=scribe_v1, language_code=hin (word timestamps are default)
Response: {"text": str, "language_code": str,
           "words": [{"text","start","end","type"}]}   type in {word, spacing, ...}
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from vex_runtime.sarvam_stt import _ffmpeg_extract, _segment

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def transcribe_with_elevenlabs(
    media_path: str | Path,
    *,
    api_key: str,
    language_code: str = "hin",
    model: str = "scribe_v1",
    verbose: bool = False,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("ElevenLabs API key is missing (ELEVENLABS_API_KEY).")
    import requests  # lazy

    src = Path(media_path)
    with tempfile.TemporaryDirectory(prefix="scribe-") as td:
        wav = Path(td) / "audio.wav"
        _ffmpeg_extract(src, wav)  # 16 kHz mono
        with open(wav, "rb") as fh:
            files = {"file": ("audio.wav", fh, "audio/wav")}
            data = {"model_id": model}
            if language_code:
                data["language_code"] = language_code
            resp = requests.post(
                SCRIBE_URL, headers={"xi-api-key": api_key}, files=files, data=data, timeout=300
            )
    if resp.status_code == 401:
        raise RuntimeError("ElevenLabs key rejected (401). Check ELEVENLABS_API_KEY.")
    if resp.status_code == 429:
        raise RuntimeError("ElevenLabs rate limit (429). Try again shortly.")
    resp.raise_for_status()
    j = resp.json()

    words: list[dict[str, Any]] = []
    for w in j.get("words", []):
        if w.get("type") not in (None, "word"):
            continue
        s, e = w.get("start"), w.get("end")
        token = (w.get("text") or "").strip()
        if s is None or e is None or not token:
            continue
        words.append({"word": token, "start": float(s), "end": float(e)})

    segments = _segment(words)
    text = (j.get("text") or " ".join(s["text"] for s in segments)).strip()
    if verbose:
        print(f"  elevenlabs scribe: lang={j.get('language_code')} "
              f"prob={j.get('language_probability')} words={len(words)} segments={len(segments)}")
    return {
        "text": text,
        "language": (j.get("language_code") or "hi"),
        "segments": segments,
        "_engine": "elevenlabs",
        "_word_timestamps": bool(words),
        "_word_timestamps_all_chunks": bool(words),
    }
