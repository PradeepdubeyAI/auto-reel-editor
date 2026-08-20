#!/usr/bin/env python3
"""B-roll moment-selection helper — run with the VEX venv Python (has `requests` + .env keys).

Reads a JSON payload file (argv[1]) = {"words": [{text,startMs,endMs}...], "durationMs": int,
"userClips": [{key, description, kind, durationMs}...]  (optional)}, calls OpenAI (gpt-4o-mini
via raw requests, the SAME key the pipeline uses), and prints a B-roll PLAN as JSON:

  {"moments": [{
      "spanStartMs", "spanEndMs",
      "type": "scene" | "text_or_stat" | "abstract",
      "primaryQuery", "fallbackQueries": [str, str], "transcriptPhrase",
      "assignedUserClipKey": str | None  -- set only when a scene moment is a strong match
          for one of the caller's userClips; None means "search stock" as before
  }...], "meta": {..., "unusedUserClipKeys": [str, ...]}}

Craft rules (hook/close protection, coverage cap, spacing) are BOTH prompted to the LLM and
re-enforced deterministically here, so the plan is safe even if the model drifts.

Never prints the API key. Does NOT import or modify pipeline.py / Vex.

Usage:  <vex-venv-python> broll_plan.py <payload.json>
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

# --- craft constants (also stated in the prompt) ---
HOOK_PROTECT_MS = 4000       # no B-roll in the first 4s — speaker owns the hook
CLOSE_PROTECT_MS = 4000      # ...or the last 4s — speaker owns the close
MIN_SPAN_MS = 1800           # a cutaway shorter than this reads as a glitch
MAX_SPAN_MS = 3000           # ...longer than this and the speaker disappears
MIN_GAP_MS = 6000            # space cutaways out (≈ one every 8-12s target)
MAX_COVERAGE = 0.60          # B-roll covers at most ~60% of runtime
VALID_TYPES = {"scene", "text_or_stat", "abstract"}

# --- graphic-card caps (Tier 2) — text must fit the 9:16 safe area ABOVE the caption zone ---
CARD_TYPES = {"stat", "phrase", "list"}
HEADLINE_MAX = 28
VALUE_MAX = 10
LIST_ITEM_MAX = 24
LIST_MAX_ITEMS = 4
_NUM_RE = re.compile(r"\d[\d,.]*\s?(?:%|x|X|k|K|m|M|bn|Bn)?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b")


def _ms(x, d=0):
    try:
        return int(round(float(x)))
    except (TypeError, ValueError):
        return d


def _title_words(text: str, n: int, cap: int) -> str:
    """First n words of `text`, title-cased-ish, capped — a distilled headline fallback."""
    words = [w for w in re.split(r"\s+", str(text or "").strip()) if w][:n]
    out = " ".join(words).strip(" .,–-")
    return out[:cap]


def clamp_card(raw, phrase: str) -> dict | None:
    """Deterministically clamp/validate an LLM card so it fits the safe area. Never echoes the
    whole caption; caps headline/value/items. Returns a card dict or None if unusable."""
    if not isinstance(raw, dict):
        raw = {}
    ct = str(raw.get("cardType", "phrase")).strip().lower()
    if ct not in CARD_TYPES:
        ct = "phrase"
    headline = " ".join(str(raw.get("headline", "")).split())[:HEADLINE_MAX]
    value = " ".join(str(raw.get("value", "")).split())[:VALUE_MAX]
    items = [" ".join(str(x).split())[:LIST_ITEM_MAX] for x in (raw.get("items") or []) if str(x).strip()][:LIST_MAX_ITEMS]

    if ct == "stat" and not value:
        m = _NUM_RE.search(phrase)
        value = (m.group(0).strip()[:VALUE_MAX] if m else "")
        if not value:
            ct = "phrase"  # no figure -> not a stat
    if ct == "list" and len(items) < 2:
        ct = "phrase"  # not enough bullets for a list
    if not headline:
        headline = _title_words(phrase, 4, HEADLINE_MAX)
    if not headline and not value and not items:
        return None
    return {
        "cardType": ct,
        "headline": headline,
        "value": value or None,
        "items": items or None,
    }


def build_timed_transcript(words: list[dict]) -> str:
    """Compact, timestamped transcript the LLM can anchor spans to (ms shown as [s.mmm])."""
    lines: list[str] = []
    cur: list[str] = []
    cur_start = None
    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        if cur_start is None:
            cur_start = _ms(w.get("startMs"))
        cur.append(text)
        end = _ms(w.get("endMs"))
        # break a line every ~3.5s or ~14 words so the model sees anchorable chunks
        if end - cur_start >= 3500 or len(cur) >= 14:
            lines.append(f"[{cur_start/1000:6.2f}s] {' '.join(cur)}")
            cur, cur_start = [], None
    if cur and cur_start is not None:
        lines.append(f"[{cur_start/1000:6.2f}s] {' '.join(cur)}")
    return "\n".join(lines)


def phrase_in_span(words: list[dict], a: int, b: int) -> str:
    hits = [str(w.get("text", "")).strip() for w in words
            if _ms(w.get("startMs")) < b and _ms(w.get("endMs")) > a]
    return " ".join(t for t in hits if t)[:160]


SYSTEM_PROMPT_HEAD = f"""You are a B-roll director for Hindi/Hinglish talking-head vertical (9:16) reels.
Given a timestamped transcript, choose the MOMENTS where a short B-roll cutaway would strengthen
the video, and describe exactly what shot to fetch.

Return ONLY a JSON object: {{"moments": [ ... ]}} where each moment has:
  "spanStartMs" (int), "spanEndMs" (int)  -- where the cutaway plays (milliseconds)
  "type": one of "scene" | "text_or_stat" | "abstract"
      - "scene": a concrete real-world shot fetchable from stock (person, place, object, action)
      - "text_or_stat": a number/quote/definition better shown as a graphic (NOT fetched yet)
      - "abstract": a vague concept with no clean literal shot (NOT fetched yet)
  "primaryQuery": a SPECIFIC stock-search phrase describing the SHOT
      (e.g. "woman typing on laptop in a coffee shop", NEVER a bare word like "office")
  "fallbackQueries": array of EXACTLY 2 alternate phrasings of the same shot
  "transcriptPhrase": the exact words spoken during the span (for display)"""

SYSTEM_PROMPT_TAIL = f"""
For a "text_or_stat" moment, ALSO include a "card" object (this becomes an on-screen graphic,
NOT stock footage):
  "card": {{
    "cardType": "stat" | "phrase" | "list",
    "headline": a SHORT distilled label/phrase (<= 28 chars) — NOT the full spoken sentence,
    "value": (stat only) the figure, e.g. "10x", "80%", "3",
    "items": (list only) an array of 2-4 very short bullets (each <= 24 chars)
  }}
CARD RULES:
  - The card is a DISTILLED visual accent. Captions already show the spoken words at the bottom,
    so the card shows only the KEY number/phrase — NEVER duplicate the caption sentence verbatim.
  - Pick cardType from the content: a figure/metric -> "stat"; an enumeration of points -> "list";
    otherwise -> "phrase".
  - Keep it tight: headline <= 28 chars; list <= 4 items, each <= 24 chars.

HARD RULES:
- PROTECT THE HOOK: no cutaway may start in the first {HOOK_PROTECT_MS} ms — the speaker stays on screen.
- PROTECT THE CLOSE: no cutaway may overlap the last {CLOSE_PROTECT_MS} ms.
- Each span between {MIN_SPAN_MS} and {MAX_SPAN_MS} ms long.
- Space cutaways out: at least {MIN_GAP_MS} ms between consecutive span starts (≈ one every 8-12s).
- Total B-roll must cover at most {int(MAX_COVERAGE*100)}% of the runtime — LESS is better than more.
- Prefer concrete nouns, emphasis, and topic shifts. When in doubt, choose FEWER, stronger moments.
- Every query must describe a filmable shot, never a generic keyword."""

# Spliced directly into the per-moment field list (between the core fields and the card/hard-rule
# tail) rather than bolted on as a trailing paragraph — a field described alongside the other
# moment fields gets followed far more reliably than the same instruction appended at the end.
USER_CLIPS_FIELD_BULLET = """
  "assignedUserClipKey": (only on "scene" moments) if this moment's topic overlaps AT ALL with one
      of the user's own uploaded clips listed below, the EXACT key of that clip (copied verbatim —
      never invent a key that isn't listed); otherwise omit/null. A user's own footage beats a
      generic stock search whenever it's a reasonable fit — do NOT withhold the assignment just
      because a stock search could also work for that moment. It's fine for a clip to go unused
      when nothing genuinely matches. Never assign the same key to more than one moment.

User clips (key: description):
{clip_list}"""


def call_llm(words: list[dict], duration_ms: int, user_clips: list[dict] | None = None) -> dict:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return {"error": "OPENAI_API_KEY not set in .env"}
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    import requests

    system = SYSTEM_PROMPT_HEAD
    if user_clips:
        clip_list = "\n".join(f"  {c['key']}: {c['description']}" for c in user_clips)
        system += USER_CLIPS_FIELD_BULLET.format(clip_list=clip_list)
    system += SYSTEM_PROMPT_TAIL

    user = (
        f"Runtime: {duration_ms} ms ({duration_ms/1000:.1f}s). "
        f"Hook window (no B-roll): 0-{HOOK_PROTECT_MS} ms. "
        f"Close window (no B-roll): {max(0, duration_ms - CLOSE_PROTECT_MS)}-{duration_ms} ms.\n\n"
        f"Transcript:\n{build_timed_transcript(words)}"
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.5,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:  # noqa: BLE001 — surface a safe message, never the key
        return {"error": str(e)[:200]}


def enforce(raw_moments: list, words: list[dict], duration_ms: int,
            valid_clip_keys: set[str] | None = None) -> list[dict]:
    """Deterministically clamp the LLM plan to the craft rules (never trust the model blindly)."""
    close_start = max(0, duration_ms - CLOSE_PROTECT_MS)
    valid_clip_keys = valid_clip_keys or set()
    used_clip_keys: set[str] = set()
    cleaned: list[dict] = []
    for m in raw_moments if isinstance(raw_moments, list) else []:
        if not isinstance(m, dict):
            continue
        a, b = _ms(m.get("spanStartMs")), _ms(m.get("spanEndMs"))
        if b <= a:
            continue
        # clamp length
        b = min(b, a + MAX_SPAN_MS)
        if b - a < MIN_SPAN_MS:
            b = a + MIN_SPAN_MS
        # hook/close protection
        if a < HOOK_PROTECT_MS:
            continue
        if b > close_start:
            b = close_start
        if b - a < MIN_SPAN_MS:
            continue
        mtype = str(m.get("type", "scene")).strip().lower()
        if mtype not in VALID_TYPES:
            mtype = "scene"
        pq = str(m.get("primaryQuery", "")).strip()
        if not pq:
            continue
        fq = [str(x).strip() for x in (m.get("fallbackQueries") or []) if str(x).strip()][:2]
        while len(fq) < 2:
            fq.append(pq)
        phrase = str(m.get("transcriptPhrase", "")).strip() or phrase_in_span(words, a, b)
        moment = {
            "spanStartMs": a, "spanEndMs": b, "type": mtype,
            "primaryQuery": pq, "fallbackQueries": fq, "transcriptPhrase": phrase,
        }
        # A user-uploaded clip can only stand in for a real scene cutaway (not a graphic card),
        # must be one of the keys the caller actually provided, and each clip is placed at most
        # once — drop anything else silently rather than let the model invent/reuse a key.
        clip_key = str(m.get("assignedUserClipKey") or "").strip()
        if mtype == "scene" and clip_key and clip_key in valid_clip_keys and clip_key not in used_clip_keys:
            moment["assignedUserClipKey"] = clip_key
            used_clip_keys.add(clip_key)
        # Non-scene moments become on-screen graphic cards. Use the LLM's card for
        # text_or_stat; synthesize a phrase card for abstract (or if the LLM omitted one).
        if mtype != "scene":
            card = clamp_card(m.get("card"), phrase)
            if card is None:
                card = {"cardType": "phrase", "headline": _title_words(phrase, 4, HEADLINE_MAX),
                        "value": None, "items": None}
            moment["card"] = card
        cleaned.append(moment)

    # spacing + coverage caps. Only "scene" moments are fetched/previewed, so the spacing
    # budget and coverage cap apply to scenes ONLY — a flagged (text_or_stat/abstract) moment
    # must not consume spacing or suppress an adjacent fetchable scene cutaway.
    cleaned.sort(key=lambda x: x["spanStartMs"])
    spaced: list[dict] = []
    last_start = -10**9
    covered = 0
    budget = MAX_COVERAGE * max(duration_ms, 1)
    for m in cleaned:
        is_scene = m["type"] == "scene"
        if is_scene and m["spanStartMs"] - last_start < MIN_GAP_MS:
            continue
        dur = m["spanEndMs"] - m["spanStartMs"]
        if is_scene and covered + dur > budget:
            continue
        spaced.append(m)
        if is_scene:
            last_start = m["spanStartMs"]
            covered += dur
    return spaced


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    words = payload.get("words") or []
    duration_ms = _ms(payload.get("durationMs")) or (
        _ms(words[-1].get("endMs")) + 2000 if words else 0
    )
    user_clips = [
        {"key": str(c.get("key") or ""), "description": str(c.get("description") or "").strip()}
        for c in (payload.get("userClips") or []) if c.get("key") and str(c.get("description") or "").strip()
    ]
    valid_clip_keys = {c["key"] for c in user_clips}
    llm = call_llm(words, duration_ms, user_clips)
    if llm.get("error"):
        print(json.dumps({"error": llm["error"]}))
        return
    moments = enforce(llm.get("moments"), words, duration_ms, valid_clip_keys)
    scene = [m for m in moments if m["type"] == "scene"]
    covered = sum(m["spanEndMs"] - m["spanStartMs"] for m in scene)
    # Computed AFTER enforce()'s spacing/coverage pass, which can still drop an assigned moment —
    # a key only counts as "used" if it survived into the final plan, not merely raw-LLM-assigned.
    used_clip_keys = {m["assignedUserClipKey"] for m in moments if "assignedUserClipKey" in m}
    print(json.dumps({
        "moments": moments,
        "meta": {
            "durationMs": duration_ms,
            "sceneCount": len(scene),
            "flaggedCount": len(moments) - len(scene),
            "coveragePct": round(100 * covered / max(duration_ms, 1), 1),
            "hookProtectMs": HOOK_PROTECT_MS,
            "closeProtectMs": CLOSE_PROTECT_MS,
            "unusedUserClipKeys": sorted(valid_clip_keys - used_clip_keys),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
