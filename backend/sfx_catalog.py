"""Static SFX catalog — plain Python registry, same pattern as caption_styles.py's
public_registry(). Files live in videos/sfx_assets/ (promoted from edit/sfx_assets/ + the
vendored pycaps presets — see that directory's NOTICE.md for provenance/licensing).

Durations are pre-measured (ffprobe, at promotion time) rather than probed per-request —
these are static bundled assets that never change at runtime.
"""
from __future__ import annotations

# id == filename stem. category groups the Fine-tune picker into a CapCut-style grid.
SFX_CATALOG = [
    {"id": "whoosh", "label": "Whoosh", "category": "whoosh", "file": "whoosh.mp3", "durationMs": 126},
    {"id": "whoosh-deep", "label": "Whoosh (deep)", "category": "whoosh", "file": "whoosh-deep.mp3", "durationMs": 2000},
    {"id": "whoosh-2", "label": "Whoosh (bright)", "category": "whoosh", "file": "whoosh-2.mp3", "durationMs": 96},
    {"id": "slide-paper", "label": "Slide", "category": "whoosh", "file": "slide-paper.mp3", "durationMs": 197},
    {"id": "hit-strong", "label": "Hit (strong)", "category": "impact", "file": "hit-strong.mp3", "durationMs": 807},
    {"id": "hit-intense", "label": "Hit (intense)", "category": "impact", "file": "hit-intense.mp3", "durationMs": 4143},
    {"id": "pop", "label": "Pop", "category": "impact", "file": "pop.mp3", "durationMs": 479},
    {"id": "pop-2", "label": "Pop (soft)", "category": "impact", "file": "pop-2.mp3", "durationMs": 243},
    {"id": "ding", "label": "Ding", "category": "notify", "file": "ding.mp3", "durationMs": 999},
    {"id": "ding-short", "label": "Ding (short)", "category": "notify", "file": "ding-short.mp3", "durationMs": 429},
    {"id": "ding-long", "label": "Ding (long)", "category": "notify", "file": "ding-long.mp3", "durationMs": 2005},
    {"id": "click", "label": "Click", "category": "notify", "file": "click.mp3", "durationMs": 759},
    {"id": "click-light", "label": "Click (light)", "category": "notify", "file": "click-light.mp3", "durationMs": 222},
    {"id": "glitch", "label": "Glitch", "category": "glitch", "file": "glitch.mp3", "durationMs": 1023},
    {"id": "glitch-static", "label": "Glitch (static)", "category": "glitch", "file": "glitch-static.mp3", "durationMs": 1396},
    {"id": "heart-beat", "label": "Heartbeat", "category": "other", "file": "heart-beat.mp3", "durationMs": 806},
    {"id": "swoosh", "label": "Swoosh", "category": "other", "file": "swoosh.mp3", "durationMs": 146},
    # --- imported from video-shotcraft (Apache-2.0); cinematic impacts/risers the original
    # 17-sound set had no equivalent for. Levels are matched at mix time by _sfx_makeup_db,
    # so these arrive consistent with the existing ones despite different mastering.
    {"id": "air-zoom-vacuum", "label": "Air Zoom Vacuum", "category": "whoosh", "file": "air-zoom-vacuum.mp3", "durationMs": 1275},
    {"id": "bass-hit-futuristic", "label": "Bass Hit Futuristic", "category": "impact", "file": "bass-hit-futuristic.mp3", "durationMs": 2750},
    {"id": "bass-hit-short", "label": "Bass Hit Short", "category": "impact", "file": "bass-hit-short.mp3", "durationMs": 1100},
    {"id": "bass-transition-pulse", "label": "Bass Transition Pulse", "category": "impact", "file": "bass-transition-pulse.mp3", "durationMs": 7290},
    {"id": "camera-shutter-hard", "label": "Camera Shutter Hard", "category": "ui", "file": "camera-shutter-hard.mp3", "durationMs": 627},
    {"id": "click-camera", "label": "Click Camera", "category": "ui", "file": "click-camera.mp3", "durationMs": 351},
    {"id": "drum-hit-trailer", "label": "Drum Hit Trailer", "category": "impact", "file": "drum-hit-trailer.mp3", "durationMs": 5630},
    {"id": "drum-impact-subtle", "label": "Drum Impact Subtle", "category": "impact", "file": "drum-impact-subtle.mp3", "durationMs": 4500},
    {"id": "drum-roll-tension", "label": "Drum Roll Tension", "category": "impact", "file": "drum-roll-tension.mp3", "durationMs": 14026},
    {"id": "glitch-electric-small", "label": "Glitch Electric Small", "category": "glitch", "file": "glitch-electric-small.mp3", "durationMs": 1023},
    {"id": "glitch-text-intro", "label": "Glitch Text Intro", "category": "glitch", "file": "glitch-text-intro.mp3", "durationMs": 4593},
    {"id": "heartbeat-single", "label": "Heartbeat Single", "category": "tension", "file": "heartbeat-single.mp3", "durationMs": 1101},
    {"id": "impact-cine", "label": "Impact Cine", "category": "impact", "file": "impact-cine.mp3", "durationMs": 4063},
    {"id": "impact-deep-whoosh", "label": "Impact Deep Whoosh", "category": "whoosh", "file": "impact-deep-whoosh.mp3", "durationMs": 4063},
    {"id": "impact-epic-trailer", "label": "Impact Epic Trailer", "category": "impact", "file": "impact-epic-trailer.mp3", "durationMs": 4866},
    {"id": "impact-movie-intro", "label": "Impact Movie Intro", "category": "impact", "file": "impact-movie-intro.mp3", "durationMs": 5994},
    {"id": "impact-transition", "label": "Impact Transition", "category": "impact", "file": "impact-transition.mp3", "durationMs": 4866},
    {"id": "impact-zoom-quick", "label": "Impact Zoom Quick", "category": "impact", "file": "impact-zoom-quick.mp3", "durationMs": 1222},
    {"id": "keyboard", "label": "Keyboard", "category": "impact", "file": "keyboard.mp3", "durationMs": 19633},
    {"id": "reverse-impact", "label": "Reverse Impact", "category": "impact", "file": "reverse-impact.mp3", "durationMs": 10077},
    {"id": "riser-cine", "label": "Riser Cine", "category": "tension", "file": "riser-cine.mp3", "durationMs": 4809},
    {"id": "riser-drama", "label": "Riser Drama", "category": "tension", "file": "riser-drama.mp3", "durationMs": 31000},
    {"id": "riser-synth", "label": "Riser Synth", "category": "tension", "file": "riser-synth.mp3", "durationMs": 9336},
    {"id": "riser-tech-choir", "label": "Riser Tech Choir", "category": "tension", "file": "riser-tech-choir.mp3", "durationMs": 17574},
    {"id": "riser-trailer", "label": "Riser Trailer", "category": "tension", "file": "riser-trailer.mp3", "durationMs": 2575},
    {"id": "shimmer-sparkle-sweep", "label": "Shimmer Sparkle Sweep", "category": "impact", "file": "shimmer-sparkle-sweep.mp3", "durationMs": 2998},
    {"id": "sparkle", "label": "Sparkle", "category": "impact", "file": "sparkle.mp3", "durationMs": 4549},
    {"id": "sub-bass-knock", "label": "Sub Bass Knock", "category": "impact", "file": "sub-bass-knock.mp3", "durationMs": 3005},
    {"id": "sweep-fast-small", "label": "Sweep Fast Small", "category": "impact", "file": "sweep-fast-small.mp3", "durationMs": 781},
    {"id": "sweep-metal-quick", "label": "Sweep Metal Quick", "category": "impact", "file": "sweep-metal-quick.mp3", "durationMs": 1722},
    {"id": "sweep-scifi-fast", "label": "Sweep Scifi Fast", "category": "impact", "file": "sweep-scifi-fast.mp3", "durationMs": 1208},
    {"id": "swoosh-quick", "label": "Swoosh Quick", "category": "whoosh", "file": "swoosh-quick.mp3", "durationMs": 781},
    {"id": "tick-percussion", "label": "Tick Percussion", "category": "impact", "file": "tick-percussion.mp3", "durationMs": 24896},
    {"id": "transition-snap", "label": "Transition Snap", "category": "impact", "file": "transition-snap.mp3", "durationMs": 574},
    {"id": "transition-soft", "label": "Transition Soft", "category": "impact", "file": "transition-soft.mp3", "durationMs": 1275},
    {"id": "typewriter", "label": "Typewriter", "category": "impact", "file": "typewriter.mp3", "durationMs": 219},
    {"id": "whoosh-big", "label": "Whoosh Big", "category": "whoosh", "file": "whoosh-big.mp3", "durationMs": 2321},
    {"id": "whoosh-fast", "label": "Whoosh Fast", "category": "whoosh", "file": "whoosh-fast.mp3", "durationMs": 1757},
]
# The Fine-tune picker renders BY CATEGORY, so a sound whose category is missing here is invisible
# in the UI even though it is in the catalog and resolves fine server-side. Keep this in step with
# every category used above -- the assertion below fails the import rather than silently hiding sounds.
CATEGORY_LABELS = {"whoosh": "Whoosh", "impact": "Impact", "notify": "Ding / Pop",
                   "glitch": "Glitch", "tension": "Tension / Riser", "ui": "UI / Camera",
                   "other": "Other"}
SFX_BY_ID = {s["id"]: s for s in SFX_CATALOG}
_unlabelled = sorted({s["category"] for s in SFX_CATALOG} - set(CATEGORY_LABELS))
assert not _unlabelled, f"SFX categories missing from CATEGORY_LABELS (would be invisible in the picker): {_unlabelled}"


def public_registry() -> dict:
    return {
        "sounds": [{**s, "url": f"/api/sfx/media/{s['file']}"} for s in SFX_CATALOG],
        "categories": [{"id": k, "label": v} for k, v in CATEGORY_LABELS.items()],
    }
