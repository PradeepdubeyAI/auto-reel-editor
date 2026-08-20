from __future__ import annotations

import config
from state import ProjectState
from tools.transcript_utils import (
    build_sentence_segments,
    clean_transcript_text,
    format_srt_timestamp,
    transcript_artifact_path,
    write_json,
)
from vex_runtime.transcription import (
    TranscriptionInstallError,
    transcribe_with_whisper,
)

import re


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _openai_text(prompt: str, *, timeout_s: int = 60, attempts: int = 2) -> str:
    """Call OpenAI chat-completions for romanization. Returns "" on failure.

    Uses OPENAI_API_KEY / OPENAI_MODEL (default gpt-4o-mini). Faster and more
    reliable than the free-tier Gemini for the batched romanization prompts.
    """
    import os
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return ""
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    import requests
    last = ""
    for _ in range(max(1, attempts)):
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                timeout=timeout_s,
            )
            if r.status_code == 200:
                last = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if last:
                    return last
        except Exception:
            continue
    return last


def _gemini_text(prompt: str, *, timeout_ms: int = 90000, attempts: int = 2) -> str:
    """Call the configured Gemini with a hard per-request timeout + one retry.

    Returns "" on any timeout/failure so callers fall back to Devanagari — the
    call can never hang indefinitely and timing is never corrupted.
    """
    import os
    # Prefer OpenAI for romanization when configured (fast + reliable); fall back to Gemini.
    provider = (os.getenv("ROMANIZE_LLM") or ("openai" if os.getenv("OPENAI_API_KEY") else "gemini")).strip().lower()
    if provider == "openai":
        _o = _openai_text(prompt, timeout_s=max(15, int(timeout_ms // 1000)), attempts=attempts)
        if _o:
            return _o
        # OpenAI failed — fall through to Gemini if it is configured
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return ""
    last = ""
    for _ in range(max(1, attempts)):
        try:
            client = genai.Client(
                api_key=config.GEMINI_API_KEY,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
            resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
            last = (getattr(resp, "text", None) or "").strip()
            if last:
                return last
        except Exception:
            continue
    return last


def _romanize_to_hinglish(texts: list[str], *, verbose: bool = False) -> list[str]:
    """Convert Hindi (Devanagari) caption lines to natural Hinglish (Roman script).

    Uses the Gemini provider already configured in Vex (config.PROVIDER == "gemini").
    Transliteration only, never translation.

    Timing-safe by construction: this only rewrites the TEXT of each line and always
    returns a list the SAME length and order as the input. Any line that cannot be
    romanized (provider off, API error, parse/count mismatch) keeps its ORIGINAL text,
    so segment timestamps can never be corrupted.
    """
    out = list(texts)
    # Only touch lines that actually contain Devanagari; leave English/ASCII untouched.
    targets = [i for i, t in enumerate(texts) if t and _DEVANAGARI.search(t)]
    if not targets:
        return out

    try:
        config.reload_settings()
    except Exception:
        return out
    if config.PROVIDER != "gemini" or not config.GEMINI_API_KEY:
        if verbose:
            print("romanize: Gemini not configured; leaving captions in Devanagari")
        return out

    numbered = "\n".join(f"{n}. {texts[i]}" for n, i in enumerate(targets, start=1))
    prompt = (
        "You transliterate Hindi subtitles into natural Hinglish for a social-media "
        "creator's captions. For each numbered line, rewrite the Hindi (Devanagari) "
        "into the Roman (English) alphabet.\n"
        "RULES:\n"
        "- Transliterate, do NOT translate the meaning.\n"
        "- Use natural, common Hinglish spellings. NO diacritics or accent marks "
        "(write 'seekhenge' not 'sIkhenge', 'kyunki' not 'kyoṅki').\n"
        "- Keep words that are already English in English (AI, ChatGPT, prompt, video, "
        "content, simple, etc.).\n"
        "- Return EXACTLY one line per input, with the SAME number and order, and "
        "NOTHING else (no notes, no blank lines, no extra numbering).\n\n"
        f"{numbered}"
    )

    raw = _gemini_text(prompt)
    if not raw:
        if verbose:
            print("romanize: Gemini returned nothing (timeout/failure); keeping Devanagari")
        return out

    parsed: dict[int, str] = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s*[\.\)\-:]\s*(.+?)\s*$", line)
        if m:
            parsed[int(m.group(1))] = m.group(2).strip()

    replaced = 0
    for n, i in enumerate(targets, start=1):
        cand = parsed.get(n)
        # Guard: accept only if it looks romanized (no Devanagari left) and non-empty.
        if cand and not _DEVANAGARI.search(cand):
            out[i] = cand
            replaced += 1
        # else: leave the original line (timing-safe fallback)
    if verbose:
        print(f"romanize: {replaced}/{len(targets)} lines romanized to Hinglish")
    return out


def _romanize_segment_words(segments: list[dict]) -> None:
    """Per-word Devanagari->Hinglish romanization for karaoke captions.

    Romanizes EACH word's text 1:1 (never merging/splitting) so per-word
    timestamps stay attached, then rebuilds each segment's text from its
    romanized words. Timing is never modified. Any word that cannot be
    romanized 1:1 keeps its original Devanagari (timing-safe). Uses the Gemini
    already configured in Vex; a single batched call covers the whole transcript.
    """
    try:
        config.reload_settings()
    except Exception:
        return
    if config.PROVIDER != "gemini" or not config.GEMINI_API_KEY:
        return

    all_words = [w for seg in segments for w in (seg.get("words") or [])]
    targets = [i for i, w in enumerate(all_words) if _DEVANAGARI.search(str(w.get("text", "")))]
    if not targets:
        return

    context = " ".join(str(seg.get("text", "")) for seg in segments)[:800]
    BATCH = 24  # small batches -> fast, reliable 1:1 count matching on gemma
    for b0 in range(0, len(targets), BATCH):
        batch = targets[b0:b0 + BATCH]
        numbered = "\n".join(f"{k}. {all_words[i]['text']}" for k, i in enumerate(batch, start=1))
        prompt = (
            "Romanize each numbered Hindi (Devanagari) WORD into natural Hinglish (Roman/English "
            "alphabet), for word-by-word video captions.\n"
            f"Context (for spelling only): {context}\n"
            "RULES:\n"
            "- Exactly ONE romanized token per input line; SAME count and SAME order. Never merge or "
            "split (e.g. चैट -> Chat, जीपीटी -> GPT).\n"
            "- Natural spellings, NO diacritics. Words already English stay English (AI, prompt, "
            "LinkedIn, output). Keep trailing punctuation.\n"
            "- Output ONLY the numbered lines.\n\n"
            f"{numbered}"
        )
        raw = _gemini_text(prompt, timeout_ms=60000)
        if not raw:
            continue  # leave this batch Devanagari; timing preserved
        parsed: dict[int, str] = {}
        for line in raw.splitlines():
            m = re.match(r"\s*(\d+)\s*[\.\)\-:]\s*(.+?)\s*$", line)
            if m:
                parsed[int(m.group(1))] = m.group(2).strip()
        for k, i in enumerate(batch, start=1):
            cand = parsed.get(k)
            if cand and not _DEVANAGARI.search(cand):
                all_words[i]["text"] = cand

    # Rebuild each segment's text from its (now romanized) words so SRT == words.
    for seg in segments:
        ws = seg.get("words") or []
        if ws:
            seg["text"] = " ".join(str(w["text"]).strip() for w in ws).strip()


# Real acronyms / brand names kept cased after sentence-case normalization (lowercased-form ->
# canonical). Everything else becomes lowercase except the first word of each sentence.
_ACRONYMS = {
    "ai": "AI", "gpt": "GPT", "chatgpt": "ChatGPT", "openai": "OpenAI",
    "linkedin": "LinkedIn", "youtube": "YouTube", "instagram": "Instagram",
    "whatsapp": "WhatsApp", "facebook": "Facebook", "google": "Google",
    "ok": "OK", "tv": "TV", "hr": "HR", "ceo": "CEO", "cto": "CTO", "cfo": "CFO",
    "upi": "UPI", "pdf": "PDF", "url": "URL", "api": "API", "seo": "SEO",
    "id": "ID", "ppt": "PPT", "kpi": "KPI", "roi": "ROI", "usa": "USA",
    "uk": "UK", "faq": "FAQ", "ui": "UI", "ux": "UX", "saas": "SaaS",
    "crm": "CRM", "sql": "SQL", "hd": "HD", "otp": "OTP", "emi": "EMI",
}
# stripped when isolating a token's core; internal hyphens/apostrophes are kept intact.
_EDGE_PUNCT = ".,!?;:\"'()[]{}…—–"
_SENT_END = (".", "!", "?", "…")


def _recase_token(token: str, *, sentence_start: bool) -> str:
    """Return `token` in natural sentence-case: lowercased, unless it's a known acronym/brand
    (canonicalised), a genuine all-caps acronym (e.g. GPT), or already-cased CamelCase
    (e.g. ChatGPT); the first word of a sentence gets a leading capital. Leading/trailing
    punctuation and internal hyphens are preserved. Devanagari (un-romanized) passes through."""
    stripped_l = token.lstrip(_EDGE_PUNCT)
    lead = token[: len(token) - len(stripped_l)]
    stripped_r = stripped_l.rstrip(_EDGE_PUNCT)
    trail = stripped_l[len(stripped_r):]
    core = stripped_r
    if not core:
        return token
    letters = re.sub(r"[^A-Za-z]", "", core)
    low = core.lower()
    if low in _ACRONYMS:
        new = _ACRONYMS[low]
    elif letters and core.isupper() and 2 <= len(letters) <= 5:
        new = core                       # genuine all-caps acronym, e.g. GPT / HR
    elif re.search(r"[A-Z]", core[1:]):
        new = core                       # already-cased CamelCase brand, e.g. ChatGPT
    else:
        new = low
        if sentence_start and letters:
            new = new[:1].upper() + new[1:]
    return lead + new + trail


def _sentencecase_hinglish(segments: list[dict]) -> None:
    """Deterministic natural-case pass over the (already romanized) words. Runs across the whole
    transcript so sentences spanning segment boundaries are cased correctly, then rebuilds each
    segment's text so SRT == words. Case only — never changes spelling or timestamps."""
    all_words = [w for seg in segments for w in (seg.get("words") or [])]
    sentence_start = True
    for w in all_words:
        tok = str(w.get("text", ""))
        if not tok:
            continue
        w["text"] = _recase_token(tok, sentence_start=sentence_start)
        core = tok.rstrip("\"')]}") .rstrip()
        if core.endswith(_SENT_END):
            sentence_start = True                       # sentence ended on this token
        elif re.search(r"[A-Za-zऀ-ॿ]", tok):
            sentence_start = False                      # a real word -> now mid-sentence
        # pure-punctuation tokens leave the state unchanged
    for seg in segments:
        ws = seg.get("words") or []
        if ws:
            seg["text"] = " ".join(str(w["text"]).strip() for w in ws).strip()


def _normalize_whisper_segments(raw_segments: list[dict]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    normalized_segments: list[dict[str, object]] = []
    normalized_words: list[dict[str, object]] = []
    word_index = 1
    for segment_index, segment in enumerate(raw_segments, start=1):
        start_sec = float(segment.get("start") or 0.0)
        end_sec = float(segment.get("end") or 0.0)
        text = clean_transcript_text(str(segment.get("text") or ""))
        if end_sec <= start_sec or not text:
            continue
        segment_words: list[dict[str, object]] = []
        for raw_word in segment.get("words") or []:
            word_text = clean_transcript_text(str(raw_word.get("word") or raw_word.get("text") or ""))
            word_start = raw_word.get("start")
            word_end = raw_word.get("end")
            if word_start is None or word_end is None or not word_text:
                continue
            if float(word_end) <= float(word_start):
                continue
            payload = {
                "index": word_index,
                "start": round(float(word_start), 3),
                "end": round(float(word_end), 3),
                "text": word_text,
                "confidence": round(float(raw_word.get("probability", 0.0)), 4)
                if raw_word.get("probability") is not None
                else None,
            }
            segment_words.append(payload)
            normalized_words.append(payload)
            word_index += 1
        normalized_segments.append(
            {
                "index": segment_index,
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "text": text,
                "word_start_index": int(segment_words[0]["index"]) if segment_words else None,
                "word_end_index": int(segment_words[-1]["index"]) if segment_words else None,
                "words": segment_words,
            }
        )
    return normalized_segments, normalized_words


def execute(params: dict, state: ProjectState) -> dict:
    import os
    try:  # make .env (SARVAM/ELEVENLABS keys, TRANSCRIBE_ENGINE) available
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    engine = (os.getenv("TRANSCRIBE_ENGINE") or params.get("engine") or "whisper").strip().lower()
    try:
        if engine == "elevenlabs":
            from vex_runtime.elevenlabs_stt import transcribe_with_elevenlabs
            result = transcribe_with_elevenlabs(
                state.working_file,
                api_key=os.getenv("ELEVENLABS_API_KEY", ""),
                language_code="hin",
                verbose=True,
            )
        elif engine == "sarvam":
            from vex_runtime.sarvam_stt import transcribe_with_sarvam
            result = transcribe_with_sarvam(
                state.working_file,
                api_key=os.getenv("SARVAM_API_KEY", ""),
                language_code=os.getenv("SARVAM_LANGUAGE", "hi-IN"),
                verbose=True,
            )
        else:
            result = transcribe_with_whisper(
                state.working_file,
                model_name=config.WHISPER_MODEL,
                configured_python=config.WHISPER_PYTHON_PATH,
                timeout_sec=config.WHISPER_TRANSCRIBE_TIMEOUT_SEC,
            )
    except TranscriptionInstallError as exc:
        return {
            "success": False,
            "message": str(exc),
            "suggestion": (
                f"[SUGGESTION]: Review the transcription runtime log at {exc.log_path}."
                if exc.log_path
                else None
            ),
            "updated_state": state,
            "tool_name": "transcribe_video",
        }
    except Exception as exc:  # Sarvam/ElevenLabs network/API errors — don't cascade
        return {
            "success": False,
            "message": f"{engine} transcription failed: {exc}",
            "suggestion": None,
            "updated_state": state,
            "tool_name": "transcribe_video",
        }

    txt_path = transcript_artifact_path(state.working_dir, "transcript.txt", for_write=True)
    srt_path = transcript_artifact_path(state.working_dir, "transcript.srt", for_write=True)
    segment_json_path = transcript_artifact_path(state.working_dir, "transcript.segments.json", for_write=True)
    words_json_path = transcript_artifact_path(state.working_dir, "transcript.words.json", for_write=True)
    sentences_json_path = transcript_artifact_path(state.working_dir, "transcript.sentences.json", for_write=True)
    raw_segments = result.get("segments", [])
    segments, words = _normalize_whisper_segments(raw_segments if isinstance(raw_segments, list) else [])
    # --- Hinglish romanization pass (Devanagari -> natural Roman script) ---
    # Gated by HINGLISH_ROMANIZE (.env). Rewrites segment TEXT and, when word-level
    # timings exist, each word's text too (per-word romanization) — timestamps are
    # never touched, so word-by-word / karaoke captions stay perfectly in sync.
    if os.getenv("HINGLISH_ROMANIZE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        _romanized = _romanize_to_hinglish([str(seg["text"]) for seg in segments], verbose=True)
        for seg, roman in zip(segments, _romanized):
            seg["text"] = roman
        _romanize_segment_words(segments)
        # Per-word romanization capitalises every token in isolation (Title Case). Normalise to
        # natural sentence-case (spelling/timing untouched) so captions don't read "Batata Hoon".
        _sentencecase_hinglish(segments)
    sentences = build_sentence_segments(words, fallback_segments=segments)
    transcript_text = (
        clean_transcript_text(" ".join(str(seg["text"]) for seg in segments))
        or clean_transcript_text(str(result.get("text") or ""))
    )
    txt_path.write_text(transcript_text + "\n", encoding="utf-8")
    srt_lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        srt_lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(segment['start'])} --> {format_srt_timestamp(segment['end'])}",
                segment["text"].strip(),
                "",
            ]
        )
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    write_json(segment_json_path, segments)
    write_json(words_json_path, words)
    write_json(sentences_json_path, sentences)
    state.artifacts["latest_transcript"] = {
        "txt_path": str(txt_path),
        "srt_path": str(srt_path),
        "segments_path": str(segment_json_path),
        "words_path": str(words_json_path),
        "sentences_path": str(sentences_json_path),
        "source": "audio_transcription",
        "segment_count": len(segments),
        "word_count": len(words),
        "sentence_count": len(sentences),
    }
    history = list(state.artifacts.get("transcript_history") or [])
    history.append(state.artifacts["latest_transcript"])
    state.artifacts["transcript_history"] = history[-10:]
    preview = "\n".join(
        f"{format_srt_timestamp(segment['start'])} {segment['text'].strip()}" for segment in segments[:10]
    )
    return {
        "success": True,
        "message": (
            f"Transcript saved to {txt_path}, {srt_path}, {segment_json_path.name}, {words_json_path.name}, "
            f"and {sentences_json_path.name}.\n{preview}"
        ),
        "suggestion": "[SUGGESTION]: Captions are ready. I can help turn them into timed overlays - reply 'yes' to apply or continue.",
        "updated_state": state,
        "tool_name": "transcribe_video",
    }
