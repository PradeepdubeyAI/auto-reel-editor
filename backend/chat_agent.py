#!/usr/bin/env python3
"""Fine-tune chat editor — run with the VEX venv Python (has `requests` + .env keys).

Reads a JSON payload file (argv[1]) = {"message": str, "history": [{"role","content"}...],
"state": {...current client-side state snapshot, see below...}}, calls OpenAI (gpt-4o-mini,
matching broll_plan.py's exact call shape/key/model-env-var), and prints:

  {"reply": str, "actions": [{"tool": str, ...params}, ...]}

This is deliberately NOT an agentic tool-EXECUTION loop (contrast vex/agent.py, which calls real
tool executors in a multi-iteration loop against a CLI project). Every mutation this app's mobile
render pipeline supports is a plain client-side React state change (see MobileApp.tsx's
swapClip/toggleRemoveClip/retimeClip/setToggles/addSfxHit/etc) with NO server-side side effect
until the user taps "Re-render" — so there is nothing for a server-side tool executor to DO. This
script's only job is structured-intent extraction: turn one chat message into a list of tool calls
the CLIENT then applies via its own already-tested functions, after the user confirms each one
(mirrors Descript's Underlord "propose, then confirm" pattern). One LLM call per turn — no
iteration, no re-prompting — since nothing here needs to observe an intermediate tool result
before deciding the next one.

Never invents a momentId/soundId/trackId/styleId not present in the state snapshot — the state
snapshot IS the whole set of things that exist to reference; enforce() drops anything invalid
rather than trusting the model.

Never prints the API key. Does NOT import or modify pipeline.py / Vex / server.py.

Usage:  <vex-venv-python> chat_agent.py <payload.json>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass

DUCKING_PRESETS = ("off", "light", "medium", "strong")


def build_system_prompt(state: dict) -> str:
    moments = state.get("moments") or []
    moment_lines = "\n".join(
        f'  {m.get("momentId")}: {m.get("spanStartMs", 0) / 1000:.1f}-{m.get("spanEndMs", 0) / 1000:.1f}s, '
        f'"{m.get("description", "")}", removed={bool(m.get("removed"))}'
        + (f', retimeable up to {m.get("maxRetimeMs")}ms in' if m.get("canRetime") else "")
        for m in moments
    ) or "  (no B-roll clips in this reel)"

    sfx_hits = state.get("sfxHits") or []
    sfx_lines = "\n".join(f'  {h.get("id")}: {h.get("soundId")} @ {h.get("atMs")}ms' for h in sfx_hits) or "  (none placed)"

    toggles = state.get("toggles") or {}
    music = state.get("music")
    music_line = (
        f'{music.get("trackId")} at {music.get("gainDb")}dB, ducking={music.get("ducking")}'
        if music else "none selected"
    )

    return f"""You are the in-app editing assistant for a short-form vertical video reel that the user
has ALREADY RENDERED once. You do not edit video directly — you translate the user's request into
a list of TOOL CALLS that the app's own UI controls would otherwise require manual taps for. The
user will see your proposed changes and confirm before anything is applied, so it's fine (expected,
even) to propose several changes for one compound request like "make the office clip shorter and
turn off zoom."

Return ONLY a JSON object: {{"reply": str, "actions": [ ... ]}}
  "reply": a short, friendly, ONE-OR-TWO-SENTENCE confirmation of what you're about to do (or, if
    nothing in-scope applies, an explanation of why — see HARD RULES).
  "actions": an array of tool calls (can be empty), each one of:

  {{"tool": "swap_broll_clip", "momentId": str}}
      -- cycle to the next stock candidate for this B-roll moment (only if it has more than one
      candidate available — you aren't told the count, just try it; the app no-ops harmlessly if
      there's only one).
  {{"tool": "set_broll_removed", "momentId": str, "removed": bool}}
      -- show/hide the B-roll clip on this moment. true = remove it, false = bring it back.
  {{"tool": "retime_broll_clip", "momentId": str, "sourceStartMs": int}}
      -- change WHICH part of a B-roll clip's own source plays (only valid for moments marked
      "retimeable" in the state below; sourceStartMs must be between 0 and that moment's max).
  {{"tool": "set_caption_style", "styleId": str}}
      -- styleId must be one of the availableCaptionStyles listed below.
  {{"tool": "set_toggle", "key": str, "on": bool}}
      -- key must be one of: captions, broll, cards, zoom, smoothTransitions, music.
  {{"tool": "set_color_grade_look", "look": str, "intensity": float}}
      -- look must be one of the availableLooks listed below; intensity is 0.0-1.0 (shown to the
      user as 0-100%). NOTE: unlike every other tool here, this one triggers a real re-grade job
      (not just a client-side state change) — mention that in your reply if you propose it.
  {{"tool": "add_sfx", "soundId": str, "atMs": int}}
      -- soundId must be one of the availableSfx ids listed below.
  {{"tool": "remove_sfx", "id": str}}
      -- id must be one of the CURRENTLY PLACED sfx hit ids listed below (not a soundId).
  {{"tool": "set_music", "trackId": str, "gainDb": float, "ducking": str}}
      -- trackId must be one of the availableMusic ids listed below (catalog ids or the user's own
      uploaded keys, both listed together). gainDb is -24 to 0 (typical background level is around
      -12). ducking must be one of: {", ".join(DUCKING_PRESETS)}.

HARD RULES:
- NEVER invent a momentId, soundId, trackId, styleId, or sfx-hit id that isn't explicitly listed in
  CURRENT STATE below. If the user refers to something ambiguous or not present, ask for
  clarification in "reply" and return an empty "actions" array — do not guess.
- This app has NO way (yet) to re-transcribe, change trim-silence aggressiveness, or change
  clean-audio strength after the first render — those are "ingest-tier" and baked in at upload
  time. If the user asks for one of these, explain in "reply" that it needs reprocessing the
  original clip, which isn't supported yet, and return an empty "actions" array. Do NOT silently
  no-op — say so.
- Prefer the FEWEST actions that satisfy the request. Don't propose changes the user didn't ask for.
- "reply" must always be present, even when "actions" is empty.

CURRENT STATE:
B-roll moments:
{moment_lines}
Toggles: {json.dumps(toggles)}
Caption style: {state.get("captionStyleId")} — availableCaptionStyles: {json.dumps(state.get("availableCaptionStyles") or [])}
Color grade: look={state.get("colorGradeLook")}, intensity={state.get("colorGradeIntensity")} — availableLooks: {json.dumps(state.get("availableLooks") or [])}
Placed sound effects:
{sfx_lines}
availableSfx: {json.dumps(state.get("availableSfx") or [])}
Music: {music_line}
availableMusic: {json.dumps(state.get("availableMusic") or [])}"""


def _valid_moment_ids(state: dict) -> dict:
    return {m.get("momentId"): m for m in (state.get("moments") or []) if m.get("momentId")}


def enforce(raw: dict, state: dict) -> dict:
    """Deterministically validate every action against the actual state snapshot — never trust
    the model's ids/enums blindly, same discipline as broll_plan.py's enforce()."""
    reply = str(raw.get("reply") or "").strip() or "Sure — one moment."
    moments = _valid_moment_ids(state)
    valid_styles = set(state.get("availableCaptionStyles") or [])
    valid_looks = set(state.get("availableLooks") or [])
    valid_sfx = set(state.get("availableSfx") or [])
    valid_placed_sfx = {h.get("id") for h in (state.get("sfxHits") or [])}
    valid_music = set(state.get("availableMusic") or [])
    valid_toggle_keys = {"captions", "broll", "cards", "zoom", "smoothTransitions", "music"}

    cleaned: list[dict] = []
    for a in raw.get("actions") or []:
        if not isinstance(a, dict):
            continue
        tool = str(a.get("tool") or "")
        try:
            if tool == "swap_broll_clip":
                mid = str(a.get("momentId") or "")
                if mid not in moments:
                    continue
                cleaned.append({"tool": tool, "momentId": mid})
            elif tool == "set_broll_removed":
                mid = str(a.get("momentId") or "")
                if mid not in moments:
                    continue
                cleaned.append({"tool": tool, "momentId": mid, "removed": bool(a.get("removed"))})
            elif tool == "retime_broll_clip":
                mid = str(a.get("momentId") or "")
                m = moments.get(mid)
                if not m or not m.get("canRetime"):
                    continue
                max_ms = int(m.get("maxRetimeMs") or 0)
                ms = max(0, min(max_ms, int(a.get("sourceStartMs", 0))))
                cleaned.append({"tool": tool, "momentId": mid, "sourceStartMs": ms})
            elif tool == "set_caption_style":
                sid = str(a.get("styleId") or "")
                if sid not in valid_styles:
                    continue
                cleaned.append({"tool": tool, "styleId": sid})
            elif tool == "set_toggle":
                key = str(a.get("key") or "")
                if key not in valid_toggle_keys:
                    continue
                cleaned.append({"tool": tool, "key": key, "on": bool(a.get("on"))})
            elif tool == "set_color_grade_look":
                look = str(a.get("look") or "")
                if look not in valid_looks:
                    continue
                intensity = max(0.0, min(1.0, float(a.get("intensity", 0.5))))
                cleaned.append({"tool": tool, "look": look, "intensity": intensity})
            elif tool == "add_sfx":
                sid = str(a.get("soundId") or "")
                if sid not in valid_sfx:
                    continue
                at_ms = max(0, int(a.get("atMs", 0)))
                cleaned.append({"tool": tool, "soundId": sid, "atMs": at_ms})
            elif tool == "remove_sfx":
                hid = str(a.get("id") or "")
                if hid not in valid_placed_sfx:
                    continue
                cleaned.append({"tool": tool, "id": hid})
            elif tool == "set_music":
                tid = str(a.get("trackId") or "")
                if tid not in valid_music:
                    continue
                gain = max(-24.0, min(0.0, float(a.get("gainDb", -12.0))))
                ducking = str(a.get("ducking", "medium"))
                if ducking not in DUCKING_PRESETS:
                    ducking = "medium"
                cleaned.append({"tool": tool, "trackId": tid, "gainDb": gain, "ducking": ducking})
            # unknown tool name -> silently dropped (never invented/executed)
        except (TypeError, ValueError):
            continue
    return {"reply": reply, "actions": cleaned}


def call_llm(message: str, history: list[dict], state: dict) -> dict:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return {"error": "OPENAI_API_KEY not set in .env"}
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    import requests

    system = build_system_prompt(state)
    messages = [{"role": "system", "content": system}]
    for h in history[-12:]:  # cap history so the prompt doesn't grow unbounded over a long chat
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            messages.append({"role": role, "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": 0.3, "response_format": {"type": "json_object"}},
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:  # noqa: BLE001 — surface a safe message, never the key
        return {"error": str(e)[:200]}


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    message = str(payload.get("message") or "").strip()
    history = payload.get("history") or []
    state = payload.get("state") or {}
    if not message:
        print(json.dumps({"error": "empty message"}))
        return
    llm = call_llm(message, history, state)
    if llm.get("error"):
        print(json.dumps({"error": llm["error"]}))
        return
    print(json.dumps(enforce(llm, state), ensure_ascii=False))


if __name__ == "__main__":
    main()
