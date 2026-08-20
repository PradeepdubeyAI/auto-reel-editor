#!/usr/bin/env python3
"""Publish-kit helper — run with the VEX venv Python (has `requests` + the .env keys).

Reads a transcript text file (argv[1]), calls OpenAI (gpt-4o-mini via raw requests, the
SAME key + endpoint the pipeline uses), and prints {title, description, hashtags} as JSON.
Never prints the API key. Does NOT import or modify pipeline.py.

Usage:  <vex-venv-python> publish_kit.py <transcript.txt>
"""
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))
except Exception:
    pass


def main() -> None:
    text = Path(sys.argv[1]).read_text()[:6000].strip()
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        print(json.dumps({"error": "OPENAI_API_KEY not set in .env"}))
        return
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"

    import requests

    prompt = (
        "You write social copy for Hindi/Hinglish talking-head Instagram/YouTube reels. "
        "From the transcript, return a JSON object with exactly these keys:\n"
        '  "title": a punchy hook title, <= 60 chars\n'
        '  "description": a 1-2 line caption\n'
        '  "hashtags": an array of 6-10 relevant hashtags (no leading #)\n'
        "Reply with ONLY the JSON object.\n\nTranscript:\n" + text
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout=45,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        out = {
            "title": str(data.get("title", "")).strip(),
            "description": str(data.get("description", "")).strip(),
            "hashtags": [
                str(h).lstrip("#").strip()
                for h in (data.get("hashtags") or [])
                if str(h).strip()
            ],
        }
        print(json.dumps(out))
    except Exception as e:  # noqa: BLE001 - surface a safe message, never the key
        print(json.dumps({"error": str(e)[:200]}))


if __name__ == "__main__":
    main()
