"""caption_styles.py — the ONE source of truth for caption styles.

A style is DATA, not code branches. The ASS generator (bake.py, Stage 2) reads a *resolved* style
and maps its fields to ASS; it never branches per style-id. Adding a style = adding a dict entry
here, touching neither renderer's logic.

Field notes:
- fontSizePx is the VISUAL on-screen size (what the desktop CSS uses directly). The mobile ASS
  generator scales it by ASS_SCALE (libass renders glyphs ~0.77x, so ASS Fontsize = visual x ~1.30).
  This keeps "amber" at ASS Fontsize 120 == visual 92, i.e. a pixel no-op vs today.
- outlineWidth is used directly as the ASS Outline value (proven to match the 9px CSS stroke).
- animation "pop_scale" bumps the ACTIVE word ~1.06x (amber's \\fs127 today); "fade" uses \\fad.
- emphasisMode "active_word" highlights the current karaoke word; "keyword" is DEFERRED (see STYLES.md)
  and treated as active_word for now; "none" = no per-word highlight (all words inactiveColor).

SYNC NOTE: when the desktop refactor (Stage 3) is built, copy this file's DATA verbatim into a
reel-studio-ui module so the two renderers never drift. Two identical files, kept in sync.
"""
from __future__ import annotations

# visual -> ASS Fontsize factor. Fontsize 120 == visual 92 (established in the caption-match POC).
ASS_SCALE = 120 / 92  # ~1.304

# S / M / L multipliers applied to a style's default fontSizePx (M = the style's own default).
SIZE_PRESETS = {"S": 0.85, "M": 1.0, "L": 1.20}

# Safe-zone clamp for position. bottomPercent is measured from the BOTTOM (higher = further up).
POSITION_MIN_PERCENT = 20   # never below 20% from bottom (platform UI / on-shirt logo live there)
POSITION_MAX_PERCENT = 65   # never higher than this (keeps the caption top out of the top-15% zone)

# ------------------------------------------------------------------ the styles --
# FINAL SET (display order): 8 native + "pill" deferred (PNG route). "clean" consolidates the former
# clean+minimal (white, thin outline, sentence case, no highlight, no motion); "minimal" removed.
STYLES: dict[str, dict] = {
    # amber — CURRENT default, EXACTLY today's look (regression-safe no-op).
    "amber": {
        "id": "amber", "label": "Amber", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 92,
        "activeColor": "#FFC233", "inactiveColor": "#FFFFFF",
        "outlineWidth": 9, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "upper", "animation": "pop_scale", "emphasisMode": "active_word",
        "bottomPercent": 22,
    },
    # hormozi — big ALL-CAPS, bright-yellow active word, heavy outline, tiny pop, higher up.
    # HIDDEN from the picker (available:false): reads too similar to "amber" (both upper + active-word
    # pop). Definition kept parked so it can be re-enabled if we want a distinct high-contrast variant.
    "hormozi": {
        "id": "hormozi", "label": "Hormozi", "available": False,
        "font": "Poppins ExtraBold", "fontSizePx": 100,
        "activeColor": "#FFEE33", "inactiveColor": "#FFFFFF",
        "outlineWidth": 13, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "upper", "animation": "pop_scale", "emphasisMode": "active_word",
        "bottomPercent": 62,
    },
    # karaoke_fill — each word FILLS left->right with fillColor over its SPOKEN duration (\kf; cs =
    # (end-start)*100). Invisible \k gap-spacers absorb inter-word pauses. One Dialogue per page.
    "karaoke_fill": {
        "id": "karaoke_fill", "label": "Karaoke", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 92,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",  # unfilled = white
        "fillColor": "#35E06A",                                # fills to green as spoken
        "outlineWidth": 8, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "upper", "animation": "none", "emphasisMode": "none",
        "karaokeFill": True,
        "bottomPercent": 22,
    },
    # neon — active word glows: crisp WHITE fill on a colored, blurred border (\3c + \blur).
    "neon": {
        "id": "neon", "label": "Neon", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 92,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",  # active fill stays crisp white
        "outlineWidth": 3, "outlineColor": "#000000",          # inactive words: thin dark outline
        "glowColor": "#22E0FF", "glowBlur": 9,                 # active word's colored blurred glow
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "upper", "animation": "pop_scale", "emphasisMode": "active_word",
        "bottomPercent": 22,
    },
    # fade — page-level \fad in/out per page (clamped so it reaches full opacity), no per-word highlight.
    "fade": {
        "id": "fade", "label": "Fade", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 88,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",
        "outlineWidth": 5, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "sentence", "animation": "fade", "emphasisMode": "none",
        "fadeMs": 250,
        "bottomPercent": 22,
    },
    # typewriter (narrative) — characters reveal left->right via INCREMENTAL ALPHA EVENTS (keeps
    # outline, no ghosts). Per-char time interpolated within each word's real span.
    "typewriter": {
        "id": "typewriter", "label": "Narrative", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 78,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",
        "outlineWidth": 5, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "sentence", "animation": "none", "emphasisMode": "none",
        "typewriter": True,
        "bottomPercent": 24,
    },
    # clean — readability-first: white, thin outline, sentence case, NO highlight, NO motion.
    # (Consolidates the former clean + minimal; "minimal" removed.)
    "clean": {
        "id": "clean", "label": "Clean", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 86,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",
        "outlineWidth": 4, "outlineColor": "#000000",
        "boxStyle": "none", "boxColor": "#000000", "boxOpacity": 0.0,
        "uppercase": "sentence", "animation": "none", "emphasisMode": "none",
        "bottomPercent": 20,
    },
    # box — white text on a semi-transparent box, no per-word highlight, sentence case.
    "box": {
        "id": "box", "label": "Box", "available": True,
        "font": "Poppins ExtraBold", "fontSizePx": 80,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",
        "outlineWidth": 0, "outlineColor": "#000000",
        "boxStyle": "box", "boxColor": "#000000", "boxOpacity": 0.55,
        "uppercase": "sentence", "animation": "none", "emphasisMode": "none",
        "bottomPercent": 20,
    },

    # -------- DEFERRED: registered but available:false. Nothing renders it (see STYLES.md). --------
    "pill": {   # rounded box — needs the PNG route, not ASS-native
        "id": "pill", "label": "Pill", "available": False,
        "font": "Poppins ExtraBold", "fontSizePx": 84,
        "activeColor": "#FFFFFF", "inactiveColor": "#FFFFFF",
        "outlineWidth": 0, "outlineColor": "#000000",
        "boxStyle": "box", "boxColor": "#000000", "boxOpacity": 0.60,
        "uppercase": "sentence", "animation": "none", "emphasisMode": "none",
        "bottomPercent": 22,
    },
}

DEFAULT_STYLE_ID = "amber"


def clamp_position(bottom_percent) -> float:
    try:
        v = float(bottom_percent)
    except (TypeError, ValueError):
        return float(STYLES[DEFAULT_STYLE_ID]["bottomPercent"])
    return max(POSITION_MIN_PERCENT, min(POSITION_MAX_PERCENT, v))


def resolve_style(style_id=None, size_px=None, bottom_percent=None) -> dict:
    """Resolve a base style from the registry + optional overrides. Unknown/unavailable ids fall
    back to the default. Position is always safe-zone clamped. Returns a fully-populated style dict
    the ASS generator consumes directly (no per-style branching downstream)."""
    base = STYLES.get(style_id or DEFAULT_STYLE_ID)
    if base is None or not base.get("available", False):
        base = STYLES[DEFAULT_STYLE_ID]
    st = dict(base)
    if size_px is not None:
        try:
            st["fontSizePx"] = max(24.0, min(200.0, float(size_px)))
        except (TypeError, ValueError):
            pass
    st["bottomPercent"] = clamp_position(st["bottomPercent"] if bottom_percent is None else bottom_percent)
    return st


def public_registry() -> dict:
    """What the frontend picker needs — served over HTTP so the UI never re-declares style data."""
    return {
        "styles": [
            {
                "id": s["id"], "label": s["label"], "available": s["available"],
                "defaultSizePx": s["fontSizePx"], "defaultBottomPercent": s["bottomPercent"],
                "uppercase": s["uppercase"], "boxStyle": s["boxStyle"],
                "activeColor": s["activeColor"], "inactiveColor": s["inactiveColor"],
                # exposed so the frontend can render a faithful CSS preview of each style
                "outlineWidth": s["outlineWidth"], "outlineColor": s["outlineColor"],
                "boxColor": s["boxColor"], "boxOpacity": s["boxOpacity"],
                # optional motion fields (default off; used by neon glow / future preview)
                "glowColor": s.get("glowColor", "#000000"), "glowBlur": s.get("glowBlur", 0),
                "fadeMs": s.get("fadeMs", 0),
                "fillColor": s.get("fillColor", "#FFFFFF"), "karaokeFill": s.get("karaokeFill", False),
                "typewriter": s.get("typewriter", False),
            }
            # only expose pickable styles — deferred/hidden ones (available:false, e.g. pill,
            # hormozi) are parked in STYLES but never shown as a picker card.
            for s in STYLES.values()
            if s.get("available")
        ],
        "sizePresets": SIZE_PRESETS,
        "position": {"min": POSITION_MIN_PERCENT, "max": POSITION_MAX_PERCENT},
        "defaultStyleId": DEFAULT_STYLE_ID,
    }
