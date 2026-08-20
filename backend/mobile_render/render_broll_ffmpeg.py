#!/usr/bin/env python3
"""render_broll_ffmpeg.py — Remotion-FREE bake (pure ffmpeg + Pillow). Used by the MOBILE path.

Same inputs + same finishing as render_broll.py (Remotion), but NO headless Chrome and NO Remotion
license: base video -> auto-zoom (crop time-expr) -> B-roll video cutaways (cover-crop) -> Ken Burns
stills (zoompan) -> graphic cards (Pillow, ported from GraphicCard.tsx) -> word-by-word captions
(ASS/libass, the byte-identical createTikTokStyleCaptions port). Then pipeline._pro_export + loudnorm
+ _qc (imported, identical to the normal reel). Mobile has no live preview, so preview==export is
irrelevant here — this is a clean win (no license, no Chrome, ~5-9x faster).

Nothing here modifies pipeline.py / render_captions.py / the POC / Remotion components.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REEL_STUDIO = ROOT / "pipeline"
FONT = ROOT / "assets" / "Poppins-ExtraBold.ttf"
W, H, FPS = 1080, 1920, 60
COMBINE_MS = 600     # exact createTikTokStyleCaptions value
WRAP_W = 900         # 1080 - 2*90 side padding
WORD_GAP = 26
AMBER_ASS = "&H0033C2FF"
WHITE_ASS = "&H00FFFFFF"
AMBER_RGB = (255, 194, 51)

# The caption-style registry lives in backend/ (the parent of this mobile_render/ dir).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from caption_styles import resolve_style, ASS_SCALE  # noqa: E402  (the ONE style registry)


def _ffprobe(path, *a):
    return subprocess.run(["ffprobe", "-v", "error", *a, str(path)], capture_output=True, text=True).stdout.strip()


def _probe(path):
    def num(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d
    w = num(_ffprobe(path, "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "default=nw=1:nk=1"), 1080)
    h = num(_ffprobe(path, "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "default=nw=1:nk=1"), 1920)
    dur = num(_ffprobe(path, "-show_entries", "format=duration", "-of", "default=nw=1:nk=1"))
    astream = _ffprobe(path, "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "default=nw=1:nk=1")
    return int(w), int(h), dur, bool(astream)


# ============================== captions (ASS) ==============================
def _load_words(transcript_path):
    wj = json.loads(Path(transcript_path).read_text())
    segs = wj.get("segments", []) if isinstance(wj, dict) else wj
    out = []
    for s in segs:
        for w in s.get("words", []):
            t = str(w.get("word") or w.get("text") or "").strip()
            if t:
                out.append({"text": t, "start": float(w["start"]), "end": float(w["end"])})
    return out


# Hindi trailing particles / auxiliaries that must NOT start a caption page — they attach to the
# PRECEDING word ("bataata hoon", "meeting mein"). Used to avoid orphaned mid-phrase fragments.
_GLUE_LEADING = {
    "hoon", "hu", "hun", "hai", "hain", "ho", "tha", "thi", "the",
    "raha", "rahi", "rahe", "hota", "hoti", "hote", "hoga", "hogi", "honge",
    "gaya", "gayi", "gaye", "sakta", "sakti", "sakte", "chahiye",
    "diya", "liya", "paya", "payi", "rakha",
    "ko", "se", "mein", "me", "par", "pe", "ka", "ki", "ke", "ne", "tak",
}
_PAGE_MIN_WORDS = 2       # never leave a single-word orphan page
_PAGE_MAX_WORDS = 6       # width guard: phrase-fixes must not overgrow a page
_PAGE_MAX_SPAN_MS = 1500  # hard span guard (COMBINE_MS stays the soft target)


def _glue_norm(tok):
    return "".join(ch for ch in str(tok).lower() if ch.isalpha())


def _ends_sentence(w):
    return str(w.get("text", "")).rstrip("\"')]}").endswith((".", "!", "?", "…"))


def _refine_pages(pages):
    """Phrase-aware cleanup of the time-based pages. Only MOVES break positions — never edits a
    word's text, order, or timing. (1) pulls a leading trailing-particle back onto the previous
    page so units like 'bataata hoon' / 'meeting mein' stay together; (2) merges any remaining
    single-word orphan page into a neighbour. Bounded by width/span guards so pages never overgrow."""
    if len(pages) < 2:
        return pages
    # 1) pull leading glue particles back onto the previous page (within guards)
    i = 1
    while i < len(pages):
        prev, cur = pages[i - 1], pages[i]
        while (cur and _glue_norm(cur[0]["text"]) in _GLUE_LEADING
               and not _ends_sentence(prev[-1])
               and len(prev) < _PAGE_MAX_WORDS
               and (round(cur[0]["end"] * 1000) - round(prev[0]["start"] * 1000)) <= _PAGE_MAX_SPAN_MS):
            prev.append(cur.pop(0))
        if not cur:
            pages.pop(i)          # fully absorbed -> drop the now-empty page
        else:
            i += 1
    # 2) merge any sub-minimum orphan page into an adjacent page (prefer previous)
    i = 0
    while i < len(pages) and len(pages) > 1:
        if len(pages[i]) < _PAGE_MIN_WORDS:
            if i > 0 and len(pages[i - 1]) + len(pages[i]) <= _PAGE_MAX_WORDS:
                pages[i - 1].extend(pages.pop(i)); continue
            if i + 1 < len(pages) and len(pages[i]) + len(pages[i + 1]) <= _PAGE_MAX_WORDS:
                pages[i + 1][:0] = pages.pop(i); continue
        i += 1
    return pages


def _page_words(words):
    """Faithful port of createTikTokStyleCaptions: new page when the current page's span
    (last-word-end - first-word-start) already exceeds COMBINE_MS (the soft target). The result is
    then refined to be phrase-aware (trailing particles stay with their word; no single-word
    orphans) — refinement only moves break positions, never words or timing."""
    pages, cur = [], []
    cf = ct = 0
    for w in words:
        s_ms, e_ms = round(w["start"] * 1000), round(w["end"] * 1000)
        if not cur:
            cur, cf, ct = [w], s_ms, e_ms
        elif (ct - cf) > COMBINE_MS:
            pages.append(cur)
            cur, cf, ct = [w], s_ms, e_ms
        else:
            cur.append(w)
            ct = e_ms
    if cur:
        pages.append(cur)
    return _refine_pages(pages)


def _wrap_lines(toks_upper, font, gap=WORD_GAP):
    lines, cur, cw = [], [], 0.0
    for i, t in enumerate(toks_upper):
        wd = font.getlength(t)
        add = wd if not cur else gap + wd
        if cur and cw + add > WRAP_W:
            lines.append(cur)
            cur, cw = [i], wd
        else:
            cur.append(i)
            cw += add
    if cur:
        lines.append(cur)
    return lines


def _ts(s):
    return f"{int(s // 3600):d}:{int((s % 3600) // 60):02d}:{s % 60:05.2f}"


def _hex_to_ass(hex_color, alpha=0):
    """#RRGGBB -> ASS &HAABBGGRR (alpha 0=opaque .. 255=fully transparent)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _apply_case(text, mode):
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    return text  # "sentence" -> leave the token's natural (romanized) case


def _build_ass(words, path, style, vdur):
    """STYLE-DRIVEN ASS. Reads a RESOLVED CaptionStyle (caption_styles.resolve_style) and maps its
    fields to ASS — NO per-style-id branching. The paging port is untouched; only appearance changes."""
    from PIL import ImageFont

    size_px = float(style["fontSizePx"])
    ass_fs = round(size_px * ASS_SCALE)                                  # visual -> ASS Fontsize
    pop_fs = round(ass_fs * 1.06) if style["animation"] == "pop_scale" else ass_fs
    case = style["uppercase"]
    inactive_ass = _hex_to_ass(style["inactiveColor"])
    active_ass = _hex_to_ass(style["activeColor"])
    emphasize = style["emphasisMode"] == "active_word"
    fade_ms = int(style.get("fadeMs", 0))  # >0 => page-level \fad (per-page emission)
    karaoke = bool(style.get("karaokeFill", False))  # per-word \kf sweep (per-page emission)
    fill_ass = _hex_to_ass(style.get("fillColor", style["inactiveColor"]))
    typewriter = bool(style.get("typewriter", False))  # incremental-alpha char reveal (per-page)
    box = style["boxStyle"] == "box"
    if box:
        # BorderStyle 3 = opaque box: OutlineColour is the box fill, Outline = padding, no stroke.
        border_style, outline_w, shadow = 3, 8, 0
        outline_ass = _hex_to_ass(style["boxColor"], alpha=round((1.0 - style["boxOpacity"]) * 255))
        back_ass = "&H00000000"
    else:
        border_style = 1
        outline_w = int(style["outlineWidth"])
        shadow = 4 if outline_w else 0
        outline_ass = _hex_to_ass(style["outlineColor"])
        back_ass = "&H73000000"
    # neon glow: the ACTIVE word gets a colored, blurred border under its crisp fill (Stage-0 validated).
    glow_blur = int(style.get("glowBlur", 0))
    glow_ass = _hex_to_ass(style.get("glowColor", "#000000"))
    glow_bord = max(4, round(glow_blur * 0.5)) if glow_blur > 0 else 0

    font_wrap = ImageFont.truetype(str(FONT), max(8, round(size_px)))    # measure wrap at the real size
    gap = max(0, round(WORD_GAP * size_px / 92))                         # scale word gap proportionally
    pages = _page_words(words)
    margin_v = int(H * style["bottomPercent"] / 100)
    header = f"""[Script Info]
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style["font"]},{ass_fs},{inactive_ass},{inactive_ass},{outline_ass},{back_ass},0,0,0,0,100,100,0,0,{border_style},{outline_w},{shadow},2,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    out = [header]
    for pi, page in enumerate(pages):
        page_end = pages[pi + 1][0]["start"] if pi + 1 < len(pages) else min(page[-1]["end"] + 0.5, vdur)
        toks = [_apply_case(w["text"], case).replace("{", "").replace("}", "") for w in page]
        wrap = _wrap_lines(toks, font_wrap, gap)
        if typewriter:
            # TYPEWRITER: incremental alpha events (Stage-0 validated) — reveal chars left->right,
            # keeping outline, no ghosts. Per-char time interpolated within each word's real span.
            # Events are CONTIGUOUS (no gap -> no blink); reveals faster than MIN_STEP are merged.
            pstart = page[0]["start"]
            line_of = {idx: li for li, ln in enumerate(wrap) for idx in ln}
            units = []  # (piece, reveal_time); piece = one char or "\N"
            prev_we = None
            for i in range(len(page)):
                wd = page[i]; ws, we = wd["start"], wd["end"]; tok = toks[i]
                if i > 0:
                    units.append(("\\N" if line_of.get(i, 0) != line_of.get(i - 1, 0) else " ", prev_we))
                C = max(1, len(tok))
                for j, ch in enumerate(tok):
                    units.append((ch, ws + (j / C) * (we - ws)))
                prev_we = we
            n = len(units); MIN_STEP = 0.03
            cur = pstart; i = 0
            while i < n:
                j = i
                while j + 1 < n and units[j + 1][1] - cur < MIN_STEP:
                    j += 1
                nxt = units[j + 1][1] if j + 1 < n else page_end
                if nxt > cur:
                    vis = "".join(u[0] for u in units[:j + 1])
                    hid = "".join(u[0] for u in units[j + 1:])
                    body = vis + (f"{{\\alpha&HFF&}}{hid}" if hid else "")
                    out.append(f"Dialogue: 0,{_ts(cur)},{_ts(nxt)},Default,,0,0,0,,{body}\n")
                cur = nxt; i = j + 1
            continue
        if karaoke:
            # KARAOKE FILL: one Dialogue/page. Each word fills left->right (\kf) over its spoken
            # duration (cs = (end-start)*100); invisible \k{gap} spacers absorb inter-word pauses so
            # the sweep stays synced to real timestamps. Dialogue Start = page's first-word start.
            pstart = page[0]["start"]
            line_of = {idx: li for li, ln in enumerate(wrap) for idx in ln}
            parts = [f"{{\\1c{fill_ass}\\2c{inactive_ass}}}"]  # \1c=filled colour, \2c=unfilled
            prev_end = pstart
            for i in range(len(page)):
                wd = page[i]
                gap_cs = max(0, round((wd["start"] - prev_end) * 100))
                word_cs = max(1, round((wd["end"] - wd["start"]) * 100))
                if i > 0:
                    parts.append("\\N" if line_of.get(i, 0) != line_of.get(i - 1, 0) else " ")
                if gap_cs > 0:
                    parts.append(f"{{\\k{gap_cs}}}")  # empty syllable consumes the pause (Stage-0 tested)
                parts.append(f"{{\\kf{word_cs}}}{toks[i]}")
                prev_end = wd["end"]
            out.append(f"Dialogue: 0,{_ts(pstart)},{_ts(page_end)},Default,,0,0,0,,{''.join(parts)}\n")
            continue
        if fade_ms > 0:
            # FADE: ONE Dialogue per PAGE with a clamped \fad so the page reaches FULL opacity —
            # each side clamped to page_dur/3 (total <= 2/3, so >= 1/3 fully opaque). No per-word slots.
            pstart = page[0]["start"]
            dur_ms = max(1, int((page_end - pstart) * 1000))
            fside = min(fade_ms, dur_ms // 3)
            page_text = "\\N".join(" ".join(toks[idx] for idx in line) for line in wrap)
            out.append(f"Dialogue: 0,{_ts(pstart)},{_ts(page_end)},Default,,0,0,0,,{{\\fad({fside},{fside})}}{page_text}\n")
            continue
        for i, w in enumerate(page):
            start = w["start"]
            end = min(page[i + 1]["start"] if i + 1 < len(page) else page_end, page_end)
            if end <= start:
                continue
            rendered = []
            for line in wrap:
                cells = []
                for idx in line:
                    if emphasize and idx == i:
                        ov = f"\\c{active_ass}"
                        if glow_blur > 0:
                            ov += f"\\3c{glow_ass}\\bord{glow_bord}\\blur{glow_blur}"
                        if pop_fs != ass_fs:
                            ov += f"\\fs{pop_fs}"
                        cells.append(f"{{{ov}}}{toks[idx]}{{\\r}}")
                    else:
                        cells.append(toks[idx])
                rendered.append(" ".join(cells))
            out.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{'\\N'.join(rendered)}\n")
    path.write_text("".join(out))


# ============================== graphic cards (Pillow port of GraphicCard.tsx) ==============================
_CARD_STYLES = {
    "ink": {"bg": [(12, 17, 22), (17, 23, 31), (9, 13, 18)], "glow": (255, 194, 51, 40), "sub": (255, 255, 255, 190)},
    "amber": {"bg": [(36, 24, 9), (53, 35, 11), (24, 15, 5)], "glow": (255, 194, 51, 66), "sub": (255, 236, 204, 217)},
    "night": {"bg": [(10, 16, 38), (24, 34, 83), (8, 12, 28)], "glow": (120, 150, 255, 51), "sub": (214, 224, 255, 209)},
}


def _grad3(size, stops):
    """3-stop vertical gradient (≈ GraphicCard's 157deg linear-gradient)."""
    from PIL import Image
    w, h = size
    img = Image.new("RGB", (w, h))
    px = img.load()
    a, b, c = stops
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.52:
            u = t / 0.52
            col = tuple(round(a[k] + (b[k] - a[k]) * u) for k in range(3))
        else:
            u = (t - 0.52) / 0.48
            col = tuple(round(b[k] + (c[k] - b[k]) * u) for k in range(3))
        for x in range(w):
            px[x, y] = col
    return img


def _font(size):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT), size)


def _center_text(d, cx, y, text, font, fill, spacing=0):
    """Draw uppercase-friendly centered text with optional letter-spacing; returns bottom y."""
    if spacing:
        widths = [d.textlength(ch, font=font) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = cx - total / 2
        for ch, wd in zip(text, widths):
            d.text((x, y), ch, font=font, fill=fill)
            x += wd + spacing
        return y + font.size
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill)
    return y + font.size


def _card_bg(card):
    """Static background layer: gradient + focal glow (GraphicCard's AbsoluteFill, which does NOT
    animate — only the content does)."""
    from PIL import Image, ImageDraw, ImageFilter

    tok = _CARD_STYLES.get(card.get("style", "ink"), _CARD_STYLES["ink"])
    img = _grad3((W, H), tok["bg"]).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gx, gy, gr = int(W * 0.5), int(H * 0.34), int(W * 0.62)
    gd.ellipse([gx - gr, gy - int(gr * 0.7), gx + gr, gy + int(gr * 0.7)], fill=tok["glow"])
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    return Image.alpha_composite(img, glow)


def _card_content(card):
    """Content layer on a TRANSPARENT full-frame canvas (text/graphics only). This is the layer
    that rises + scales in (GraphicCard's safeArea div), composited over the static bg."""
    from PIL import Image, ImageDraw

    style = card.get("style", "ink")
    tok = _CARD_STYLES.get(style, _CARD_STYLES["ink"])
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    ctype = card.get("cardType", "phrase")
    headline = (card.get("headline") or "").strip()
    cx = W // 2
    top = int(H * 0.10)

    def shadow_text_center(y, text, font, fill, spacing=0):
        # soft shadow: dark offset copy
        _center_text(d, cx + 3, y + 4, text, font, (0, 0, 0, 120), spacing)
        return _center_text(d, cx, y, text, font, fill, spacing)

    if ctype == "stat":
        value = (card.get("value") or "—")
        vs = 420 if len(value) <= 2 else 340 if len(value) <= 4 else 260 if len(value) <= 6 else 200
        vf = _font(vs)
        vw = d.textlength(value, font=vf)
        vy = int(H * 0.22)
        d.text((cx - vw / 2 + 3, vy + 4), value, font=vf, fill=(0, 0, 0, 120))
        d.text((cx - vw / 2, vy), value, font=vf, fill=AMBER_RGB + (255,))
        if headline:
            shadow_text_center(vy + vs + 20, headline.upper(), _font(52), tok["sub"], spacing=3)
    elif ctype == "list":
        items = card.get("items") or []
        y = int(H * 0.14)
        lx = int(W * 0.11)
        if headline:
            d.text((lx + 3, y + 4), headline, font=_font(62), fill=(0, 0, 0, 120))
            d.text((lx, y), headline, font=_font(62), fill=(255, 255, 255, 255))
            y += 62 + 34
        bf = _font(56)
        for it in items[:4]:
            d.rounded_rectangle([lx, y + 12, lx + 22, y + 34], radius=6, fill=AMBER_RGB + (255,))
            d.text((lx + 46 + 3, y + 4), str(it), font=bf, fill=(0, 0, 0, 120))
            d.text((lx + 46, y), str(it), font=bf, fill=(255, 255, 255, 255))
            y += 56 + 26
    else:  # phrase
        words = headline.split()
        last = words[-1] if len(words) > 1 else ""
        head = " ".join(words[:-1]) if len(words) > 1 else ""
        size = 150 if len(headline) <= 12 else 120 if len(headline) <= 20 else 96
        pf = _font(size)
        # amber rule bar
        d.rounded_rectangle([cx - 42, top, cx + 42, top + 8], radius=4, fill=AMBER_RGB + (242,))
        y = top + 30
        if last:
            full = head + " " + last
            fw = d.textlength(full, font=pf)
            hw = d.textlength(head + " ", font=pf)
            x = cx - fw / 2
            d.text((x + 3, y + 4), head, font=pf, fill=(0, 0, 0, 120))
            d.text((x, y), head, font=pf, fill=(255, 255, 255, 255))
            d.text((x + hw + 3, y + 4), last, font=pf, fill=(0, 0, 0, 120))
            d.text((x + hw, y), last, font=pf, fill=AMBER_RGB + (255,))
        else:
            shadow_text_center(y, headline, pf, AMBER_RGB + (255,))
    return img


def _render_card_clip(card, dur, out):
    """Pre-render an alpha card clip (dur seconds): static bg + content that RISES (26->0px) and
    SCALES (0.972->1.0) in over ~0.167s with cubic ease-out (matching GraphicCard's entrance), a
    quick whole-card fade-in, and the whole-card fade-OUT (kept). Overlaid like the Ken Burns clip
    in the main graph. qtrle = alpha so the fades reveal the base."""
    bg_png = out.with_name(out.stem + "_bg.png")
    ct_png = out.with_name(out.stem + "_ct.png")
    _card_bg(card).save(bg_png)
    _card_content(card).save(ct_png)
    p = "min(t/0.167\\,1)"                        # entrance progress, clip-local t
    ease = f"(1-pow(1-{p}\\,3))"                  # cubic ease-out 0->1
    se = f"(0.972+0.028*{ease})"                  # scale 0.972 -> 1.0
    rise = f"(26*pow(1-{p}\\,3))"                 # translateY 26 -> 0 px
    fc = (
        f"[1:v]format=rgba,scale=w='round(iw*{se}/2)*2':h='round(ih*{se}/2)*2':eval=frame[cs];"
        f"[0:v]format=rgba[bg];"
        f"[bg][cs]overlay=x='(main_w-overlay_w)/2':y='(main_h-overlay_h)/2+{rise}'[card];"
        f"[card]fade=t=in:st=0:d=0.1:alpha=1,fade=t=out:st={max(0.0, dur - 0.15)}:d=0.15:alpha=1[vout]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{dur}", "-i", str(bg_png),
         "-loop", "1", "-t", f"{dur}", "-i", str(ct_png), "-filter_complex", fc,
         "-map", "[vout]", "-r", str(FPS), "-c:v", "qtrle", "-an", str(out)],
        check=True,
    )


# ============================== ken burns clip ==============================
def _render_kenburns(still, dur_s, out, fade=True):
    """`fade=True` (the "Smooth cutaways" toggle) softens this overlay's entry/exit into a quick
    ~120-150ms alpha crossfade against the base video, instead of an instant hard-cut pop — the
    SAME technique _render_card_clip already uses for graphic cards, just not previously extended
    to Ken Burns stills. `out` must be a qtrle-compatible container (.mov) when fade=True, since
    an alpha channel is required for the fade to reveal the base rather than fading to black."""
    frames = max(1, int(dur_s * FPS))
    vf = (f"scale=8000:-1,zoompan=z='min(zoom+0.0007,1.18)':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}")
    if fade:
        vf += (f",format=rgba,fade=t=in:st=0:d=0.12:alpha=1,"
               f"fade=t=out:st={max(0.0, dur_s - 0.15):.3f}:d=0.15:alpha=1")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(still),
                        "-vf", vf, "-t", f"{dur_s}", "-c:v", "qtrle", "-an", str(out)], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(still),
                        "-vf", vf, "-t", f"{dur_s}", "-pix_fmt", "yuv420p", "-an", str(out)], check=True)


# ============================== auto-zoom expression ==============================
# Overshoot figures ported from the shot-recipe library's flip/settle cards (their 12-degree
# overshoot on a 180-degree flip is ~6.7% of travel; ~12% reads better on a scale move, where the
# cue is subtler than a rotation). Fraction OF THE TRAVEL, not of the scale.
_ZOOM_OVERSHOOT = 0.12
_ZOOM_SETTLE_FRAC = 0.45   # settle takes 45% of the rise, matching their 18f rise + 8f rebound


def _zoom_expr(zooms, fps):
    """zoompan z-expression, evaluated PER OUTPUT FRAME (time = on/fps).

    IMPORTANT: ffmpeg's `crop` filter evaluates its w/h expressions ONCE at init (only x/y
    re-evaluate per frame), so a time-varying `crop=iw/(z):ih/(z)` does NOT zoom — it freezes at
    the t=0 value (z=1, full frame). zoompan re-evaluates `z` every frame, so the punch-in is real.
    Cubic ease-in-out ramp (matches Remotion ZoomPan's easeInOutCubic); scale stays >= 1.0.
    Commas are protected by the surrounding z='...' quotes in the filtergraph — NOT backslash-escaped."""
    tm = f"(on/{fps})"
    z = "1"
    for s in zooms:
        a, b = s["startMs"] / 1000, s["endMs"] / 1000
        scale = max(1.0, float(s.get("targetScale", 1.1)))
        if s.get("style") == "seam_mask":
            # INSTANT at `a`, then ease back out. This one must not ramp in: its whole job is to
            # make the framing differ across the frame the cut lands on, which is what stops a
            # leftover gesture reading as a glitch. A ramp would arrive after the cut and mask
            # nothing. The ease-out afterwards sits in continuous footage, so it goes unnoticed.
            hold = min(1.2, max(0.2, (b - a) * 0.45))
            t0 = a + hold
            p = f"min(max(({tm}-{t0})/{max(b - t0, 0.01):.3f},0),1)"
            ease = f"(if(lt({p},0.5),4*pow({p},3),1-pow(-2*{p}+2,3)/2))"
            val = f"({scale}-({scale}-1)*{ease})"
            z = f"if(between({tm},{a},{b}),{val},{z})"
            continue
        punch = s.get("style") == "quick_punch"
        ramp = 0.28 if punch else 0.6
        p = f"min(({tm}-{a})/{ramp},1)"  # normalized progress 0..1
        # easeInOutCubic(p) = p<0.5 ? 4p^3 : 1 - (-2p+2)^3/2
        ease = f"(if(lt({p},0.5),4*pow({p},3),1-pow(-2*{p}+2,3)/2))"
        if punch:
            # OVERSHOOT then settle, for the punch only. Landing exactly on target reads as a stiff
            # stop -- the shot-recipe library this figure comes from is explicit that a move with no
            # overshoot "reads as a stopped clock", and that its own first attempt at 8% was
            # imperceptible before being raised to ~12% of the travel. Applied to the TRAVEL, not the
            # scale, so a subtle 1.14 punch overshoots ~1.7% and a strong 1.42 one ~5% -- proportional
            # to how hard the move was in the first place.
            #
            # Deliberately NOT applied to slow_push: that move is meant to drift imperceptibly and
            # never "lands", so a rebound would only draw attention to a push designed to hide.
            over = 1.0 + (float(scale) - 1.0) * (1.0 + _ZOOM_OVERSHOOT)
            settle = ramp * _ZOOM_SETTLE_FRAC
            t_out = a + ramp
            q = f"min(max(({tm}-{t_out})/{settle},0),1)"
            qe = f"(1-pow(1-{q},3))"                      # ease-out back down to target
            val = f"(({over})-(({over})-({scale}))*{qe})"
            rise = f"(1+({over}-1)*{ease})"               # ramp UP to the overshoot peak
            z = (f"if(between({tm},{a},{t_out}),{rise},"
                 f"if(between({tm},{t_out},{b}),{val},{z}))")
        else:
            val = f"(1+({scale}-1)*{ease})"
            z = f"if(between({tm},{a},{b}),{val},{z})"
    return z


# Anchor clamp: keep the crop's center inside a safe interior zone so a face near the frame
# edge can't drag the crop rectangle fully into black (mirrors vex's own motion-director clamp
# and chrislema/videoeditor's face-anchored punch-in -- see reel-editing-playbook references/
# 04-zoom.md). 0.5 = dead center = the old (pre-anchor) behavior.
ANCHOR_MIN, ANCHOR_MAX = 0.18, 0.82


def _detect_zoom_anchor(base_video: str, start_s: float, end_s: float) -> tuple[float, float] | None:
    """Find where the subject's face actually is during a zoom span, so the punch-in lands on
    them instead of on whatever happens to be at frame-center. Samples a handful of frames
    across the span, runs a Haar-cascade face detector on each (downscaled for speed), discards
    detections that disagree with the median (a false positive on e.g. a hand or a graphic),
    and averages the rest into one stable normalized (fx, fy) point in SOURCE-frame coordinates
    (matches chrislema/videoeditor's verified approach). Returns None (caller falls back to
    frame-center) if opencv isn't available or no face is found in at least 2 samples."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    n_samples = 6
    span = max(end_s - start_s, 0.001)
    times = [start_s + span * i / max(n_samples - 1, 1) for i in range(n_samples)]
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return None
    pts: list[tuple[float, float]] = []
    for t in times:
        try:
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", base_video,
                 "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, check=True, timeout=10,
            ).stdout
        except Exception:
            continue
        if not raw:
            continue
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        h, w = img.shape[:2]
        scale = 0.25
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        if len(faces) == 0:
            continue
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face wins
        sh, sw = gray.shape[:2]
        pts.append(((fx + fw / 2) / sw, (fy + fh / 2) / sh))
    if len(pts) < 2:
        return None
    xs = sorted(p[0] for p in pts)
    ys = sorted(p[1] for p in pts)
    med_x, med_y = xs[len(xs) // 2], ys[len(ys) // 2]
    keep = [(x, y) for x, y in pts if abs(x - med_x) <= 0.30 and abs(y - med_y) <= 0.30]  # drop outliers
    if not keep:
        keep = pts
    cx = sum(x for x, _ in keep) / len(keep)
    cy = sum(y for _, y in keep) / len(keep)
    return (min(max(cx, ANCHOR_MIN), ANCHOR_MAX), min(max(cy, ANCHOR_MIN), ANCHOR_MAX))


def _zoom_xy_expr(zooms, fps, axis):
    """Companion to `_zoom_expr`: builds the zoompan x= or y= expression so each segment's crop
    is centered on that segment's detected face anchor (falling back to frame-center when no
    face was found), instead of always being frame-center. Uses the SAME active-window
    if/between chain shape as `_zoom_expr` so both stay in sync frame-by-frame."""
    tm = f"(on/{fps})"
    dim = "iw" if axis == "x" else "ih"
    expr = f"({dim}/2-({dim}/zoom/2))"  # default: dead center, outside any zoom window
    for s in zooms:
        a, b = s["startMs"] / 1000, s["endMs"] / 1000
        frac = s.get("anchorX" if axis == "x" else "anchorY")
        frac = 0.5 if frac is None else min(max(float(frac), ANCHOR_MIN), ANCHOR_MAX)
        seg = f"min(max({frac}*{dim}-({dim}/zoom/2),0),{dim}-({dim}/zoom))"
        expr = f"if(between({tm},{a},{b}),{seg},{expr})"
    return expr


# ============================== main bake ==============================
MUSIC_DUCK_PRESETS = {
    # off = no sidechain at all, music plays flat (still volume-trimmed into the mix).
    "off": None,
    "light": {"threshold": 0.09, "ratio": 4},
    "medium": {"threshold": 0.05, "ratio": 8},
    "strong": {"threshold": 0.03, "ratio": 14},
}


def render_broll_ffmpeg_reel(base_video, transcript_json_path, output_path, accepted_broll, *,
                             zooms=None, bottom_percent=None, style="word-focus", size_px=None,
                             captions_off=False, fps=60, run_qc=True, smooth_transitions=True,
                             sfx_hits=None, music=None, log=print):
    """`smooth_transitions` ("Smooth cutaways" in Setup) softens B-roll/Ken-Burns overlay entry
    and exit into a quick alpha crossfade instead of an instant hard-cut pop — graphic cards
    already did this (_render_card_clip's baked-in fade); this extends the SAME technique to the
    other two overlay kinds. Scoped to overlay boundaries only — jump cuts on the underlying
    talking-head base are untouched, since a hard cut is the correct, expected treatment there.

    `sfx_hits` (Fine-tune's SFX sheet) = [{"path", "atMs", "gainDb"}, ...] — each mixed onto the
    voice track via the SAME amix/adelay/dynaudnorm pattern edit/build_aitool_audio.py proved out.

    `music` (Fine-tune's Music sheet) = {"path", "gainDb", "ducking": "off"|"light"|"medium"|"strong"}
    or None — looped to cover the full base duration, sidechain-ducked under the voice track via
    `sidechaincompress` (named presets map to threshold/ratio pairs; "off" skips the sidechain and
    just plays the bed flat), then mixed in alongside any SFX hits.

    Both empty/absent (the overwhelming common case) keep the original `-map 0:a? -c:a copy`
    passthrough byte-for-byte unchanged — this only branches when SFX or music is actually placed."""
    # `style` is a caption-style id (legacy "word-focus" -> the default "amber"). Resolve it +
    # size/position overrides ONCE here; _build_ass consumes the resolved style with no branching.
    # bottom_percent=None / size_px=None => the STYLE's own default (so "hormozi" gets its 62%, etc.).
    caption_style = resolve_style(style, size_px, bottom_percent)
    def emit(o):
        log(json.dumps(o, ensure_ascii=False))

    def note(m):
        emit({"event": "log", "text": str(m)})

    base_video, output_path = str(base_video), str(output_path)
    zooms = zooms or []
    accepted_broll = accepted_broll or []
    sfx_hits = sfx_hits or []
    music = music or None
    if not accepted_broll and not zooms and captions_off and not sfx_hits and not music:
        raise ValueError("nothing to bake")

    width, height, vdur, has_audio = _probe(base_video)
    words = _load_words(transcript_json_path)
    caption_end = max((w["end"] for w in words), default=0.0)

    work = Path(tempfile.mkdtemp(prefix="broll_ff_"))
    final = Path(output_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 1) captions
        ass = work / "captions.ass"
        if not captions_off and words:
            _build_ass(words, ass, caption_style, vdur)

        # 2) build inputs + overlay filters (b-roll video / ken burns / cards)
        emit({"event": "step", "name": "Composite", "status": "running"})
        inputs = ["-i", base_video]
        pre = []      # per-input prep filters
        overlays = []  # (label, start_s, end_s)
        idx = 1
        for i, b in enumerate(accepted_broll):
            a, bb = int(b["startMs"]) / 1000, int(b["endMs"]) / 1000
            dur = max(0.1, bb - a)
            kind = b.get("kind")
            if kind == "card":
                clip = work / f"card_{i}.mov"
                _render_card_clip(b.get("card") or {}, dur, clip)  # bg + rise+scale content + fades
                inputs += ["-i", str(clip)]
                pre.append(f"[{idx}:v]setpts=PTS+{a}/TB[m{idx}]")  # place at span start (alpha kept)
            elif kind == "image":
                # .mov (not .mp4) once fade=True needs an alpha-carrying qtrle stream; the mov
                # muxer handles both the qtrle (fade) and yuv420p (no-fade) cases fine, so always
                # use it here rather than branching the extension on the toggle.
                kb = work / f"kb_{i}.mov"
                _render_kenburns(b["path"], dur, kb, fade=smooth_transitions)
                inputs += ["-i", str(kb)]
                pre.append(f"[{idx}:v]setpts=PTS+{a}/TB[m{idx}]")
            else:  # video
                src_path = Path(b["path"])
                offset_ms = int(b.get("sourceStartMs", 0) or 0)
                if offset_ms > 0:
                    _, _, src_dur, _ = _probe(src_path)
                    if src_dur and offset_ms >= (src_dur * 1000 - 200):
                        offset_ms = 0  # in-point leaves no usable footage; fall back to the start
                if offset_ms > 0:
                    # -stream_loop restarts the file from ITS OWN t=0 on every loop after the
                    # first pass (a leading -ss only seeks the initial play-through, verified
                    # empirically) — so a retimed in-point must be pre-extracted before looping,
                    # otherwise a clip needing >1 loop to fill `dur` would jump back to frame 0.
                    trimmed = work / f"broll_trim_{i}.mp4"
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", f"{offset_ms / 1000:.3f}", "-i", str(src_path),
                         "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast",
                         "-crf", "18", str(trimmed)],
                        check=True, capture_output=True,
                    )
                    src_path = trimmed
                inputs += ["-stream_loop", "-1", "-i", str(src_path)]
                # Fade filters run BEFORE the setpts shift below, so `st=` is relative to this
                # overlay's OWN local clock (0..dur), not the base video's absolute time — exactly
                # like _render_card_clip's pre-rendered fade, just inline in the live filtergraph
                # instead of a separate subprocess (this source is a continuous/looped stream, not
                # a pre-trimmed isolated clip, so it can't be pre-rendered the same way).
                fade_vf = (
                    f"format=rgba,fade=t=in:st=0:d=0.12:alpha=1,"
                    f"fade=t=out:st={max(0.0, dur - 0.15):.3f}:d=0.15:alpha=1,"
                ) if smooth_transitions else ""
                pre.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                           f"crop={W}:{H},{fade_vf}setpts=PTS+{a}/TB[m{idx}]")
            overlays.append((f"m{idx}", a, bb))
            idx += 1

        # 3) filtergraph: base -> zoom (zoompan, per-frame) -> chain overlays -> captions
        if zooms:
            # Anchor each zoom on the subject's actual face position (once per span, via a few
            # sampled frames) instead of always punching in on frame-center -- a center-anchored
            # zoom only looks right by luck when the speaker happens to be dead-center in frame.
            anchored_zooms = []
            for z in zooms:
                a_s, b_s = int(z["startMs"]) / 1000, int(z["endMs"]) / 1000
                # "center" is an explicit user choice, not a missing value -- skip detection so
                # _zoom_xy_expr falls back to its 0.5 centre default instead of re-finding the face.
                anchor = None if str(z.get("anchor", "")) == "center" else _detect_zoom_anchor(base_video, a_s, b_s)
                z2 = dict(z)
                if anchor:
                    z2["anchorX"], z2["anchorY"] = anchor
                anchored_zooms.append(z2)
            zexpr = _zoom_expr(anchored_zooms, fps)
            xexpr = _zoom_xy_expr(anchored_zooms, fps, "x")
            yexpr = _zoom_xy_expr(anchored_zooms, fps, "y")
            fc = [f"[0:v]fps={fps},zoompan=z='{zexpr}':d=1:"
                  f"x='{xexpr}':y='{yexpr}':s={W}x{H}:fps={fps}[bg]"]
        else:
            fc = [f"[0:v]scale={W}:{H}[bg]"]
        fc += pre
        cur = "bg"
        for k, (label, a, bb) in enumerate(overlays):
            nxt = f"ov{k}"
            fc.append(f"[{cur}][{label}]overlay=0:0:enable='between(t\\,{a}\\,{bb})'[{nxt}]")
            cur = nxt
        if not captions_off and words:
            fc.append(f"[{cur}]ass={ass}:fontsdir={FONT.parent}[vout]")
        else:
            fc.append(f"[{cur}]null[vout]")

        # SFX + Music (Fine-tune sheets) — mix onto the voice track. Left untouched (0:a?
        # passthrough, -c:a copy below) whenever neither is placed, which is the overwhelming
        # common case; only branches into a real audio filtergraph when needed.
        audio_map, audio_codec_args = "0:a?", ["-c:a", "copy"]
        if sfx_hits or music:
            mix_labels = ["[voice]"]
            fc.append("[0:a]anull[voice]" if has_audio else f"anullsrc=r=48000:cl=stereo:d={vdur:.3f}[voice]")
            for k, hit in enumerate(sfx_hits):
                inputs += ["-i", str(hit["path"])]
                at_ms = max(0, int(hit.get("atMs", 0)))  # adelay takes milliseconds directly
                # Compress each hit BEFORE its gain, not after. A short accent cannot be made louder
                # in the mix by turning it up: its peak is already near full scale, so alimiter after
                # the mix simply eats the gain -- measured, a boosted click gained 0.9dB of audibility
                # and no more. Compressing raises the sound's DENSITY at the same peak, which is level
                # the limiter has no reason to remove. Order matters and the reverse was tried first:
                # compressing after the boost squashes an already-hot signal and made every weak sound
                # WORSE (~0dB). Measured with this order: slide-paper 2.2 -> 10.3dB, ding-short
                # 4.8 -> 13.4dB, whoosh 7.8 -> 10.5dB, with nothing regressing.
                fc.append(f"[{idx}:a]acompressor=threshold=0.05:ratio=6:attack=1:release=80:makeup=8,"
                          f"volume={float(hit.get('gainDb', 0.0))}dB,adelay={at_ms}|{at_ms}[sfx{k}]")
                mix_labels.append(f"[sfx{k}]")
                idx += 1
            if music:
                # -stream_loop -1 makes the input an infinitely-repeating stream (same trick the
                # video b-roll branch already uses to pad a short clip to fill its window); atrim
                # then cuts that infinite stream down to exactly the base's own duration. No -ss
                # is involved here (always starts the bed at its own t=0), so this doesn't hit the
                # "-ss + stream_loop only seeks once" pitfall the B-roll retime fix had to work
                # around — empirically re-verified for the AUDIO case specifically before shipping.
                inputs += ["-stream_loop", "-1", "-i", str(music["path"])]
                gain = float(music.get("gainDb", -12.0))
                fc.append(f"[{idx}:a]volume={gain}dB,atrim=0:{vdur:.3f},asetpts=PTS-STARTPTS[mbed]")
                preset = MUSIC_DUCK_PRESETS.get(music.get("ducking", "medium"))
                if preset:
                    fc.append(f"[mbed][voice]sidechaincompress=threshold={preset['threshold']}:"
                              f"ratio={preset['ratio']}:attack=5:release=300[mducked]")
                    mix_labels.append("[mducked]")
                else:
                    mix_labels.append("[mbed]")
                idx += 1
            # duration=longest (not "first"): [voice] is [0]'s own demuxed audio stream, which
            # can end a hair before `vdur` on some mp4/mov mux quirks (container duration vs.
            # actual audio-sample length) — "first" would truncate the music bed (atrim'd to
            # exactly vdur) right along with it. "longest" guarantees at least vdur; the final
            # encode's own "-t vdur" below safely clips anything longer regardless.
            # normalize=0 and a LIMITER, not dynaudnorm. Both defaults were actively undoing the
            # music gain the user set:
            #
            #  * amix defaults to normalize=1, which divides every input by the input count -- with
            #    voice + music that is -6dB on BOTH, so the mix arrived quiet and the explicit
            #    volume={gain}dB above stopped meaning what it says.
            #  * dynaudnorm then pulled the quiet mix back up. It is a DYNAMIC normaliser with no
            #    notion of which stream is the bed, so wherever the voice pauses it lifts whatever is
            #    left -- the music -- toward the target level. That is exactly the reported "preview
            #    is quiet, the render comes out loud": the bed swells into every gap between lines,
            #    and the sidechain ducking is fighting a normaliser that re-inflates what it ducked.
            #
            # With normalize=0 the music sits exactly `gainDb` under the voice as asked, the limiter
            # only catches true peaks (voice + bed summing over 0dBFS) instead of riding the level,
            # and program loudness is left to the single loudnorm pass in _pro_export where it
            # belongs -- one thing setting level, not three fighting over it.
            fc.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:"
                      f"dropout_transition=0:normalize=0,alimiter=limit=0.97:level=false[aout]")
            audio_map, audio_codec_args = "[aout]", ["-c:a", "aac", "-b:a", "192k"]

        filter_complex = ";".join(fc)

        composite = work / "composite.mp4"
        note(f"Base {width}x{height} {vdur:.1f}s; {len(overlays)} overlay(s), {len(zooms)} zoom(s), "
             f"{len(sfx_hits)} sfx hit(s), music={'on' if music else 'off'}; pure ffmpeg.")
        t0 = time.time()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                        "-filter_complex", filter_complex, "-map", "[vout]", "-map", audio_map,
                        "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", *audio_codec_args, "-t", f"{vdur:.3f}", str(composite)], check=True)
        note(f"Composite rendered in {time.time() - t0:.0f}s (no Chrome).")
        emit({"event": "step", "name": "Composite", "status": "done"})

        # 4) SAME finishing as the normal reel — imported, not reimplemented
        sys.path.insert(0, str(REEL_STUDIO))
        import pipeline  # noqa: E402
        emit({"event": "step", "name": "Export", "status": "running"})
        pipeline._pro_export(str(composite), str(final), note)
        emit({"event": "step", "name": "Export", "status": "done"})

        qc_checks = []
        if run_qc:
            emit({"event": "step", "name": "QC", "status": "running"})
            qc = pipeline._qc(str(final), caption_end, note)
            qc_checks = [{"label": c["name"], "pass": c["pass"], "detail": c["detail"]} for c in qc["checks"]]
            emit({"event": "step", "name": "QC", "status": "done", "all_pass": qc["all_pass"]})

        emit({"event": "done", "output": str(final), "qc": qc_checks, "engine": "ffmpeg"})
        return {"output": str(final), "qc": qc_checks}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    payload = json.loads(Path(sys.argv[1]).read_text())
    render_broll_ffmpeg_reel(
        payload["base_video"], payload["transcript_json_path"], payload["output_path"],
        payload["accepted_broll"], zooms=payload.get("zooms"),
        bottom_percent=payload.get("bottom_percent"), style=payload.get("style", "word-focus"),
        size_px=payload.get("size_px"),
        captions_off=payload.get("captions_off", False), fps=payload.get("fps", 60),
        run_qc=payload.get("run_qc", True),
        smooth_transitions=payload.get("smoothTransitions", True),
        sfx_hits=payload.get("sfxHits"), music=payload.get("music"),
    )


if __name__ == "__main__":
    main()
