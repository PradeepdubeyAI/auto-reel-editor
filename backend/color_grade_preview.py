#!/usr/bin/env python3
"""Color-grade look preview helper — run with the VEX venv Python (needs numpy + color_grading).

Reads a JSON payload file (argv[1]) = {"video_path": str, "timestamp_s": float,
"looks": [str, ...], "intensity": float}, renders ONE small preview thumbnail per look using
the fast non-shot-aware grading path (cheap enough for an interactive picker — the real
shot-aware grade only runs when a look is actually applied via regrade), and prints
{"previews": {look: base64_png_or_null, ...}} as JSON.

Does NOT modify the source video. Does NOT import or modify reel-studio/pipeline.py's main
run_pipeline path — only reads.

Usage:  <vex-venv-python> color_grade_preview.py <payload.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REEL_STUDIO = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(REEL_STUDIO))


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    try:
        import pipeline  # adds vex/ to sys.path itself (see reel-studio/pipeline.py's own setup)

        previews = pipeline.render_look_previews(
            payload["video_path"],
            float(payload.get("timestamp_s", 2.0)),
            list(payload.get("looks") or []),
            intensity=float(payload.get("intensity", 0.5)),
        )
        print(json.dumps({"previews": previews}))
    except Exception as e:  # noqa: BLE001 - surface a safe message, never a raw traceback to the client
        print(json.dumps({"error": str(e)[:300]}))


if __name__ == "__main__":
    main()
