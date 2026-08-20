#!/usr/bin/env python3
"""B-roll candidate fetch + rank — run with the VEX venv Python (has `requests` + .env keys).

Reads a JSON payload file (argv[1]) = {"query": str, "page": int, "spanMs": int,
"orientation": "portrait"|"landscape"}, over-fetches ~8-12 candidates (VIDEOS *and* IMAGES,
portrait-first) from Pexels + Pixabay, dedupes, ranks them with a simple heuristic, and prints:

  {"candidates": [{
      id, source, kind: "video"|"image", thumbUrl, mediaUrl,
      width, height, orientation, durationSec, score, creator, sourceUrl
  }...], "providers": {pexels: bool, pixabay: bool}, "query": str, "page": int}

API request shapes are BORROWED read-only from vex/broll_intelligence.py (Pexels
/v1/videos/search + /v1/search, Pixabay /api/videos + /api). Vex is NOT imported/modified.

RANKING IS ISOLATED in rank_candidates(query, span_ms, candidates) so a CLIP semantic
re-ranker (Phase B4) can replace ONLY that function without touching fetch or the UI.

Never prints the API key.

Usage:  <vex-venv-python> broll_fetch.py <payload.json>
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

PER_PROVIDER = 6  # over-fetch: 6 videos + 6 images per provider, then rank the union down
REQ_TIMEOUT = 20  # per provider request; 4 sequential fetchers stay under the server's guard


def _int(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def orientation_of(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "landscape"
    if h > w * 1.1:
        return "portrait"
    if w > h * 1.1:
        return "landscape"
    return "square"


# ------------------------------------------------------------------ Pexels ---
def pexels_headers() -> dict | None:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return None
    return {"Authorization": key, "Accept": "application/json", "User-Agent": "ReelStudio/1.0"}


def fetch_pexels_videos(query, orientation, page, requests) -> list[dict]:
    h = pexels_headers()
    if not h:
        return []
    params = {"query": query, "orientation": orientation, "size": "medium",
              "per_page": PER_PROVIDER, "page": max(1, page)}
    r = requests.get("https://api.pexels.com/v1/videos/search", headers=h, params=params, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos") or []:
        # best mp4 file: hd, orientation-matching, highest res
        best, best_score = None, None
        for f in v.get("video_files") or []:
            if str(f.get("file_type") or "").lower() != "video/mp4":
                continue
            w, hh = _int(f.get("width")), _int(f.get("height"))
            if w <= 0 or hh <= 0:
                continue
            s = (18 if orientation_of(w, hh) == orientation else 0) + \
                (20 if str(f.get("quality")).lower() == "hd" else 8) + min(w * hh / 2_073_600, 3) * 12
            if best_score is None or s > best_score:
                best, best_score = f, s
        if not best:
            continue
        w, hh = _int(best.get("width")), _int(best.get("height"))
        pics = v.get("video_pictures") or []
        thumb = str(v.get("image") or (pics[0].get("picture") if pics else "") or "")
        out.append({
            "id": f"pexels-v-{v.get('id')}", "source": "pexels", "kind": "video",
            "thumbUrl": thumb, "mediaUrl": str(best.get("link") or ""),
            "width": w, "height": hh, "orientation": orientation_of(w, hh),
            "durationSec": float(v.get("duration") or 0), "creator": str((v.get("user") or {}).get("name") or ""),
            "sourceUrl": str(v.get("url") or ""),
        })
    return [c for c in out if c["mediaUrl"]]


def fetch_pexels_photos(query, orientation, page, requests) -> list[dict]:
    h = pexels_headers()
    if not h:
        return []
    params = {"query": query, "orientation": orientation, "per_page": PER_PROVIDER, "page": max(1, page)}
    r = requests.get("https://api.pexels.com/v1/search", headers=h, params=params, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("photos") or []:
        src = p.get("src") or {}
        w, hh = _int(p.get("width")), _int(p.get("height"))
        out.append({
            "id": f"pexels-p-{p.get('id')}", "source": "pexels", "kind": "image",
            "thumbUrl": str(src.get("medium") or src.get("small") or src.get("tiny") or ""),
            "mediaUrl": str(src.get("large2x") or src.get("large") or src.get("original") or ""),
            "width": w, "height": hh, "orientation": orientation_of(w, hh),
            "durationSec": 0.0, "creator": str(p.get("photographer") or ""),
            "sourceUrl": str(p.get("url") or ""),
        })
    return [c for c in out if c["mediaUrl"]]


# ----------------------------------------------------------------- Pixabay ---
def fetch_pixabay_videos(query, orientation, page, requests) -> list[dict]:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    min_w, min_h = (720, 960) if orientation == "portrait" else (960, 540)
    params = {"key": key, "q": query[:100], "video_type": "all", "min_width": min_w,
              "min_height": min_h, "safesearch": "true", "order": "popular",
              "per_page": max(3, PER_PROVIDER), "page": max(1, page)}
    r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    out = []
    for v in r.json().get("hits") or []:
        best, best_score = None, None
        for quality, f in (v.get("videos") or {}).items():
            w, hh = _int(f.get("width")), _int(f.get("height"))
            if not str(f.get("url") or "").strip() or w <= 0 or hh <= 0:
                continue
            s = (18 if orientation_of(w, hh) == orientation else 0) + \
                {"large": 24, "medium": 20, "small": 12, "tiny": 6}.get(str(quality), 4) + \
                min(w * hh / 2_073_600, 3) * 12
            if best_score is None or s > best_score:
                best, best_score = {**f, "_q": quality}, s
        if not best:
            continue
        w, hh = _int(best.get("width")), _int(best.get("height"))
        out.append({
            "id": f"pixabay-v-{v.get('id')}", "source": "pixabay", "kind": "video",
            "thumbUrl": str(best.get("thumbnail") or ""), "mediaUrl": str(best.get("url") or ""),
            "width": w, "height": hh, "orientation": orientation_of(w, hh),
            "durationSec": float(v.get("duration") or 0), "creator": str(v.get("user") or ""),
            "sourceUrl": str(v.get("pageURL") or ""),
        })
    return [c for c in out if c["mediaUrl"]]


def fetch_pixabay_images(query, orientation, page, requests) -> list[dict]:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    params = {"key": key, "q": query[:100], "image_type": "photo",
              "orientation": "vertical" if orientation == "portrait" else "horizontal",
              "safesearch": "true", "order": "popular",
              "per_page": max(3, PER_PROVIDER), "page": max(1, page)}
    r = requests.get("https://pixabay.com/api/", params=params, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    out = []
    for p in r.json().get("hits") or []:
        w, hh = _int(p.get("imageWidth")), _int(p.get("imageHeight"))
        out.append({
            "id": f"pixabay-p-{p.get('id')}", "source": "pixabay", "kind": "image",
            "thumbUrl": str(p.get("previewURL") or p.get("webformatURL") or ""),
            "mediaUrl": str(p.get("largeImageURL") or p.get("webformatURL") or ""),
            "width": w, "height": hh, "orientation": orientation_of(w, hh),
            "durationSec": 0.0, "creator": str(p.get("user") or ""),
            "sourceUrl": str(p.get("pageURL") or ""),
        })
    return [c for c in out if c["mediaUrl"]]


# ------------------------------------------------------- ranking (isolated) --
def rank_candidates(query: str, span_ms: int, candidates: list[dict]) -> list[dict]:
    """Heuristic rank — HIGHER score first. This is the ONLY place ranking logic lives; a
    CLIP/semantic re-ranker (Phase B4) replaces this function body while keeping the same
    (query, span_ms, candidates)->sorted-candidates contract, so the UI never changes.

    Signals: orientation match (portrait > croppable landscape), resolution, video
    duration-fit to the span, and a small provider/kind prior."""
    span_s = max(0.1, span_ms / 1000.0)
    for c in candidates:
        w, hh = c["width"], c["height"]
        score = 0.0
        # orientation: portrait ideal (no crop); landscape usable (center-croppable); square meh
        score += {"portrait": 40.0, "landscape": 18.0, "square": 10.0}.get(c["orientation"], 0.0)
        # resolution vs a 1080x1920 target (capped)
        score += min((w * hh) / (1080 * 1920), 2.5) * 14
        # duration fit for video: needs to cover the span; too short is penalised, long is fine
        if c["kind"] == "video":
            d = c["durationSec"]
            if d <= 0:
                score += 4
            elif d < span_s:
                score -= (span_s - d) * 6          # can't cover the whole cutaway
            else:
                score += 10 - min(d - span_s, 10)  # mild preference for close-fit
        else:
            score += 6  # a still is always "long enough"; Ken Burns adds the motion
        c["score"] = round(score, 2)
    # stable order preserves provider search rank within equal scores
    return sorted(candidates, key=lambda c: c["score"], reverse=True)


def dedupe(cands: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cands:
        key = c["id"] or c["mediaUrl"]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    query = str(payload.get("query", "")).strip()
    page = _int(payload.get("page"), 1) or 1
    span_ms = _int(payload.get("spanMs"), 3000)
    orientation = str(payload.get("orientation", "portrait")).strip().lower()
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"
    if not query:
        print(json.dumps({"candidates": [], "error": "empty query"}))
        return

    import requests
    fetchers = [fetch_pexels_videos, fetch_pexels_photos, fetch_pixabay_videos, fetch_pixabay_images]
    # Pixabay REQUIRES the key in the URL query string, so a provider HTTP error's message
    # embeds the full URL (incl. the key). Redact every secret out of ANY error text before
    # it can be recorded/printed/returned — never leak a key value, not even a partial.
    secrets = [s for s in (os.getenv("PIXABAY_API_KEY", "").strip(),
                           os.getenv("PEXELS_API_KEY", "").strip(),
                           os.getenv("OPENAI_API_KEY", "").strip()) if s]

    def redact(msg: str) -> str:
        for sec in secrets:
            msg = msg.replace(sec, "<redacted>")
        return msg

    cands: list[dict] = []
    errors: list[str] = []
    for fn in fetchers:
        try:
            cands.extend(fn(query, orientation, page, requests))
        except Exception as e:  # noqa: BLE001 — one provider failing must not kill the rest
            errors.append(f"{fn.__name__}: {redact(str(e))[:120]}")

    ranked = rank_candidates(query, span_ms, dedupe(cands))
    result = {
        "candidates": ranked,
        "query": query, "page": page, "orientation": orientation,
        "providers": {
            "pexels": bool(os.getenv("PEXELS_API_KEY", "").strip()),
            "pixabay": bool(os.getenv("PIXABAY_API_KEY", "").strip()),
        },
        "errors": errors,
    }
    # Total outage (every provider errored, zero candidates) must NOT look like an empty
    # search: surface a top-level error so the server 502s and the UI shows a real failure.
    if not ranked and errors:
        result["error"] = "all providers failed: " + "; ".join(errors)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
