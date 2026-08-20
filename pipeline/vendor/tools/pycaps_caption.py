"""pycaps caption step for Vex.

Replaces Vex's built-in ASS subtitle burn with pycaps' professional, animated,
word-by-word captions. pycaps is installed in its OWN isolated venv (it needs a
browser renderer we don't want in Vex's env), so this step invokes the pycaps
CLI via subprocess. It consumes an EXTERNAL word-timed transcript (whisper_json)
— the Sarvam/Scribe + Hinglish transcript Vex already produced — so pycaps never
re-transcribes with Whisper.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
import os

PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PYCAPS_BIN = Path(os.environ.get("PYCAPS_BIN", str(PIPELINE_ROOT / ".pycaps-venv" / "bin" / "pycaps")))


def caption_with_pycaps(
    video: str | Path,
    whisper_json: str | Path,
    output: str | Path,
    *,
    template: str = "word-focus",
    layout_align: str = "bottom",
    layout_offset: float = -0.2,   # fraction of height; negative lifts into the safe zone
    quality: str = "high",
) -> str:
    """Render word-by-word captions from a whisper_json transcript onto `video`.

    Returns the output path on success; raises subprocess.CalledProcessError on failure
    (caller can fall back to Vex's ASS burn).
    """
    out = Path(output)
    if out.exists():          # pycaps refuses to overwrite
        out.unlink()
    cmd = [
        str(PYCAPS_BIN), "render",
        "--input", str(video),
        "--output", str(out),
        "--transcript", str(whisper_json),
        "--transcript-format", "whisper_json",
        "--template", template,
        "--layout-align", layout_align,
        "--layout-align-offset", str(layout_offset),
        "--video-quality", quality,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return str(out)
