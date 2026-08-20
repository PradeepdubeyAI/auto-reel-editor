"""Static music catalog — same pattern as sfx_catalog.py's public_registry(). Files live in
videos/music_assets/ — 40 CC0 (public-domain-dedication) tracks from OpenGameArt/Freesound
(individually verified per-track) and the FreePD mirror (repo-wide CC0 1.0 Universal LICENSE,
verified directly). CC0 is the only license confirmed to permit bundling the raw file into a
downstream product for other end-users to pick from — see that directory's NOTICE.md for full
per-track source/composer attribution (not required by CC0, but good practice) and the specific
license verification done for each batch.

Durations are pre-measured (ffprobe, at promotion time) — these are static bundled assets that
never change at runtime.
"""
from __future__ import annotations

MUSIC_CATALOG = [
    # cinematic
    {"id": "fantasy-orchestral-theme", "label": "Fantasy Orchestral Theme", "category": "cinematic", "file": "fantasy-orchestral-theme.mp3", "durationMs": 191687},
    {"id": "determined-pursuit", "label": "Determined Pursuit", "category": "cinematic", "file": "determined-pursuit.mp3", "durationMs": 108000},
    {"id": "legend-will-rise", "label": "A Legend Will Rise", "category": "cinematic", "file": "legend-will-rise.mp3", "durationMs": 69642},
    {"id": "field-of-dreams", "label": "Field of Dreams", "category": "cinematic", "file": "field-of-dreams.mp3", "durationMs": 84637},
    {"id": "adventure", "label": "Adventure", "category": "cinematic", "file": "adventure.mp3", "durationMs": 84793},
    {"id": "heroic-adventure", "label": "Heroic Adventure", "category": "cinematic", "file": "heroic-adventure.mp3", "durationMs": 142942},
    {"id": "fanfare-x", "label": "Fanfare X", "category": "cinematic", "file": "fanfare-x.mp3", "durationMs": 39608},
    {"id": "lonely-mountain", "label": "Lonely Mountain", "category": "cinematic", "file": "lonely-mountain.mp3", "durationMs": 190380},
    {"id": "overture", "label": "Overture", "category": "cinematic", "file": "overture.mp3", "durationMs": 98717},
    # upbeat
    {"id": "city-sunshine", "label": "City Sunshine", "category": "upbeat", "file": "city-sunshine.mp3", "durationMs": 184999},
    {"id": "funshine", "label": "Funshine", "category": "upbeat", "file": "funshine.mp3", "durationMs": 165068},
    {"id": "happy-whistling-ukulele", "label": "Happy Whistling Ukulele", "category": "upbeat", "file": "happy-whistling-ukulele.mp3", "durationMs": 123324},
    {"id": "inspiration", "label": "Inspiration", "category": "upbeat", "file": "inspiration.mp3", "durationMs": 138371},
    {"id": "screen-saver", "label": "Screen Saver", "category": "upbeat", "file": "screen-saver.mp3", "durationMs": 182073},
    {"id": "prophet-energetic", "label": "Energetic Montage", "category": "upbeat", "file": "prophet-energetic.mp3", "durationMs": 34534},
    # scoring (underscore / background)
    {"id": "slice-of-life", "label": "Slice of Life", "category": "scoring", "file": "slice-of-life.mp3", "durationMs": 140760},
    {"id": "the-lagoon", "label": "The Lagoon", "category": "scoring", "file": "the-lagoon.mp3", "durationMs": 154000},
    {"id": "travelers-notebook", "label": "Traveler's Notebook", "category": "scoring", "file": "travelers-notebook.mp3", "durationMs": 123089},
    {"id": "magic-in-the-garden", "label": "Magic in the Garden", "category": "scoring", "file": "magic-in-the-garden.mp3", "durationMs": 139703},
    {"id": "dreams-of-vain", "label": "Dreams of Vain", "category": "scoring", "file": "dreams-of-vain.mp3", "durationMs": 96810},
    {"id": "meadow-thoughts", "label": "Meadow Thoughts", "category": "scoring", "file": "meadow-thoughts.mp3", "durationMs": 149744},
    {"id": "calm-background", "label": "Calm Background", "category": "scoring", "file": "calm-background.mp3", "durationMs": 107288},
    # romance / calm piano
    {"id": "lovely-piano-song", "label": "Lovely Piano Song", "category": "romance", "file": "lovely-piano-song.mp3", "durationMs": 95869},
    {"id": "nostalgic-piano", "label": "Nostalgic Piano", "category": "romance", "file": "nostalgic-piano.mp3", "durationMs": 196336},
    {"id": "winter", "label": "Winter", "category": "romance", "file": "winter.mp3", "durationMs": 313000},
    {"id": "pond", "label": "Pond", "category": "romance", "file": "pond.mp3", "durationMs": 152398},
    {"id": "landras-dream", "label": "Landra's Dream", "category": "romance", "file": "landras-dream.mp3", "durationMs": 88921},
    # comedy / quirky
    {"id": "the-entertainer", "label": "The Entertainer", "category": "comedy", "file": "the-entertainer.mp3", "durationMs": 193384},
    {"id": "silly-boy", "label": "Silly Boy", "category": "comedy", "file": "silly-boy.mp3", "durationMs": 95530},
    {"id": "frogs-legs-rag", "label": "Frogs Legs Rag", "category": "comedy", "file": "frogs-legs-rag.mp3", "durationMs": 169744},
    {"id": "busybody", "label": "Busybody", "category": "comedy", "file": "busybody.mp3", "durationMs": 86400},
    # world
    {"id": "bollywood-groove", "label": "Bollywood Groove", "category": "world", "file": "bollywood-groove.mp3", "durationMs": 150073},
    {"id": "sunny-rasta", "label": "Sunny Rasta", "category": "world", "file": "sunny-rasta.mp3", "durationMs": 140166},
    {"id": "village-tarantella", "label": "Village Tarantella", "category": "world", "file": "village-tarantella.mp3", "durationMs": 53029},
    {"id": "nomadic-sunset", "label": "Nomadic Sunset", "category": "world", "file": "nomadic-sunset.mp3", "durationMs": 190679},
    # electronic
    {"id": "backbeat", "label": "Backbeat", "category": "electronic", "file": "backbeat.mp3", "durationMs": 46211},
    {"id": "favorite", "label": "Favorite", "category": "electronic", "file": "favorite.mp3", "durationMs": 175000},
    {"id": "meditating-beat", "label": "Meditating Beat", "category": "electronic", "file": "meditating-beat.mp3", "durationMs": 157231},
    {"id": "chronos", "label": "Chronos", "category": "electronic", "file": "chronos.mp3", "durationMs": 129222},
    # corporate
    {"id": "montage", "label": "Montage", "category": "corporate", "file": "montage.mp3", "durationMs": 44577},
    # --- imported from video-shotcraft (Apache-2.0)
    {"id": "bgm-tech-house", "label": "Bgm Tech House", "category": "cinematic", "file": "bgm-tech-house.mp3", "durationMs": 288692},
    {"id": "cat-walk", "label": "Cat Walk", "category": "cinematic", "file": "cat-walk.mp3", "durationMs": 123990},
    {"id": "g-eazy-nba-type", "label": "G Eazy Nba Type", "category": "cinematic", "file": "g-eazy-nba-type.mp3", "durationMs": 104312},
    {"id": "house-vibez", "label": "House Vibez", "category": "cinematic", "file": "house-vibez.mp3", "durationMs": 111464},
    {"id": "tonight-hiphop", "label": "Tonight Hiphop", "category": "cinematic", "file": "tonight-hiphop.mp3", "durationMs": 113476},
]
CATEGORY_LABELS = {
    "cinematic": "Cinematic", "upbeat": "Upbeat", "scoring": "Scoring / Background",
    "romance": "Romance / Calm", "comedy": "Comedy / Quirky", "world": "World",
    "electronic": "Electronic", "corporate": "Corporate",
}
MUSIC_BY_ID = {t["id"]: t for t in MUSIC_CATALOG}

DUCKING_PRESETS = ("off", "light", "medium", "strong")


def public_registry() -> dict:
    return {
        "tracks": [{**t, "url": f"/api/music/media/{t['file']}"} for t in MUSIC_CATALOG],
        "categories": [{"id": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "duckingPresets": list(DUCKING_PRESETS),
    }
