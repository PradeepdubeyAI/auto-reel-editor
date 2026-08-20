from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subtitles.styles import SubtitleStyle, resolve_subtitle_style


NAMED_COLORS = {
    "white": "#FFFFFF",
    "black": "#000000",
    "yellow": "#FACC15",
    "red": "#EF4444",
    "green": "#22C55E",
    "blue": "#3B82F6",
    "cyan": "#22D3EE",
    "magenta": "#D946EF",
    "orange": "#F97316",
}

ALIGNMENTS = {
    "bottom": 2,
    "center": 5,
    "middle": 5,
    "top": 8,
}


@dataclass(frozen=True)
class SubtitleRenderPlan:
    source_srt_path: str
    ass_path: str
    style: SubtitleStyle
    width: int
    height: int
    position: str
    source_cues: int
    rendered_events: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_srt_path": self.source_srt_path,
            "ass_path": self.ass_path,
            "style": self.style.to_dict(),
            "width": int(self.width),
            "height": int(self.height),
            "position": self.position,
            "source_cues": int(self.source_cues),
            "rendered_events": int(self.rendered_events),
        }


def parse_srt(path: str | Path) -> list[dict[str, float | str]]:
    raw_text = Path(path).read_text(encoding="utf-8-sig").strip()
    if not raw_text:
        return []
    blocks = re.split(r"\r?\n\r?\n+", raw_text)
    cues: list[dict[str, float | str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_line = next((line for line in lines if "-->" in line), "")
        if not timestamp_line:
            continue
        start_raw, end_raw = [part.strip() for part in timestamp_line.split("-->", 1)]
        start_sec = _parse_timestamp(start_raw)
        end_sec = _parse_timestamp(end_raw)
        text_start = lines.index(timestamp_line) + 1
        text = _clean_caption_text(" ".join(lines[text_start:]))
        if text and end_sec > start_sec:
            cues.append({"start": start_sec, "end": end_sec, "text": text})
    return cues


def compile_subtitles_to_ass(
    srt_path: str | Path,
    ass_path: str | Path,
    *,
    width: int,
    height: int,
    style_name: str | None = "clean_pop",
    position: str = "bottom",
    font_size: int | None = None,
    font_color: str | None = None,
    outline_color: str | None = None,
    emphasis_color: str | None = None,
    background_opacity: float | None = None,
    max_words_per_caption: int | None = None,
    max_lines: int | None = None,
    case: str | None = None,
) -> SubtitleRenderPlan:
    source = Path(srt_path)
    target = Path(ass_path)
    style = resolve_subtitle_style(
        style_name,
        font_size=font_size,
        font_color=font_color,
        outline_color=outline_color,
        emphasis_color=emphasis_color,
        background_opacity=background_opacity,
        max_words_per_caption=max_words_per_caption,
        max_lines=max_lines,
        case=case,
    )
    normalized_position = _normalize_position(position)
    cues = parse_srt(source)
    events = _caption_events(cues, style=style, width=width)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _ass_document(
            events,
            style=style,
            width=max(int(width or 0), 1),
            height=max(int(height or 0), 1),
            position=normalized_position,
        ),
        encoding="utf-8",
    )
    return SubtitleRenderPlan(
        source_srt_path=str(source),
        ass_path=str(target),
        style=style,
        width=max(int(width or 0), 1),
        height=max(int(height or 0), 1),
        position=normalized_position,
        source_cues=len(cues),
        rendered_events=len(events),
    )


def _parse_timestamp(value: str) -> float:
    cleaned = str(value or "").strip().replace(",", ".")
    match = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?", cleaned)
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4) or "0"
    millis = int(fraction.ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _normalize_position(position: str) -> str:
    normalized = str(position or "bottom").strip().lower()
    if normalized not in ALIGNMENTS:
        raise ValueError("Subtitle position must be one of: bottom, center, top")
    return "center" if normalized == "middle" else normalized


def _clean_caption_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", str(text or ""))
    cleaned = cleaned.replace("\\N", " ").replace("\\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _caption_events(
    cues: list[dict[str, float | str]],
    *,
    style: SubtitleStyle,
    width: int,
) -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    max_chars = max(12, min(style.max_chars_per_line, int(max(width, 1) / 42)))
    # One-accent-word-per-line highlight: only active when the style's secondary
    # (emphasis) colour actually differs from the primary colour.
    accent_inline = _ass_inline_color(style.secondary_color)
    primary_inline = _ass_inline_color(style.primary_color)
    highlight_on = accent_inline != primary_inline
    accent_tag = "{\\1c" + accent_inline + "}" if highlight_on else None
    reset_tag = "{\\1c" + primary_inline + "}" if highlight_on else None
    for cue in cues:
        start = float(cue["start"])
        end = float(cue["end"])
        text = _apply_case(str(cue["text"]), style.case)
        words = [word for word in re.split(r"\s+", text) if word]
        if not words or end <= start:
            continue
        duration = end - start
        caption_count = max(
            1,
            math.ceil(len(words) / max(float(style.max_words_per_caption), 1.0)),
            math.ceil(len(text) / max(float(max_chars * style.max_lines), 1.0)),
            math.ceil(duration / max(float(style.max_duration_sec), 0.2)),
        )
        caption_count = min(caption_count, len(words))
        for index in range(caption_count):
            word_start = int(len(words) * index / caption_count)
            word_end = len(words) if index == caption_count - 1 else int(len(words) * (index + 1) / caption_count)
            chunk = words[word_start:word_end]
            if not chunk:
                continue
            piece_start = start + duration * (index / caption_count)
            piece_end = start + duration * ((index + 1) / caption_count)
            events.append(
                {
                    "start": round(piece_start, 3),
                    "end": round(piece_end, 3),
                    "text": _wrap_lines(
                        chunk,
                        max_chars_per_line=max_chars,
                        max_lines=style.max_lines,
                        highlight_index=_pick_highlight_index(chunk) if highlight_on else None,
                        accent_tag=accent_tag,
                        reset_tag=reset_tag,
                    ),
                }
            )
    return events


_HIGHLIGHT_STOPWORDS = {
    # common Hindi/Hinglish function words
    "hai", "hain", "ho", "hu", "hoon", "ka", "ki", "ke", "ko", "se", "me", "mein",
    "par", "aur", "ya", "to", "toh", "ye", "yeh", "wo", "woh", "na", "nahi", "bhi",
    "hi", "kya", "kyu", "kyun", "kyunki", "jo", "is", "us", "ek", "bas", "kar", "karo",
    # common English function words
    "the", "a", "an", "of", "and", "or", "in", "on", "it", "you", "your", "i", "we",
    "that", "this", "for", "with", "so", "but", "if", "as", "at", "by", "be", "do",
}


def _pick_highlight_index(words: list[str]) -> int | None:
    """Pick ONE salient word to accent per caption line.

    Priority: a number/metric > longest content word. Function words score 0 so
    they are never highlighted. Returns None when nothing worth highlighting.
    """
    best_i: int | None = None
    best_score = 0
    for i, word in enumerate(words):
        token = re.sub(r"[^0-9A-Za-zऀ-ॿ]", "", str(word))
        if not token:
            continue
        low = token.lower()
        if re.search(r"\d", token):
            score = 100 + len(token)
        elif low in _HIGHLIGHT_STOPWORDS:
            score = 0
        else:
            score = len(token)
        if score > best_score:
            best_score = score
            best_i = i
    return best_i


def _apply_case(text: str, mode: str) -> str:
    if mode == "uppercase":
        return text.upper()
    if mode == "title":
        return text.title()
    return text


def _wrap_lines(
    words: list[str],
    *,
    max_chars_per_line: int,
    max_lines: int,
    highlight_index: int | None = None,
    accent_tag: str | None = None,
    reset_tag: str | None = None,
) -> str:
    # Escape words up-front; wrapping decisions use the plain (escaped) lengths so
    # that inline colour override tags never affect line-break math.
    escaped = [_escape_ass_text(word) for word in words]

    def render(i: int) -> str:
        if i == highlight_index and accent_tag and reset_tag:
            return f"{accent_tag}{escaped[i]}{reset_tag}"
        return escaped[i]

    lines_idx: list[list[int]] = []
    current: list[int] = []
    idx = 0
    total = len(words)
    while idx < total and len(lines_idx) < max_lines:
        candidate = " ".join(escaped[j] for j in current + [idx]).strip()
        if current and len(candidate) > max_chars_per_line:
            lines_idx.append(current)
            current = [idx]
        else:
            current.append(idx)
        idx += 1
    if current:
        if len(lines_idx) < max_lines:
            lines_idx.append(current)
        elif lines_idx:
            lines_idx[-1].extend(current)
    if idx < total and lines_idx:
        lines_idx[-1].extend(range(idx, total))
    rendered_lines = [" ".join(render(i) for i in line) for line in lines_idx if line]
    return "\\N".join(line for line in rendered_lines if line.strip())


def _escape_ass_text(text: str) -> str:
    escaped = str(text or "").replace("{", "(").replace("}", ")")
    escaped = escaped.replace("\r", " ").replace("\n", "\\N")
    return escaped.strip()


def _ass_document(
    events: list[dict[str, float | str]],
    *,
    style: SubtitleStyle,
    width: int,
    height: int,
    position: str,
) -> str:
    font_size = _font_size(style, width=width, height=height)
    margin_l = max(16, int(width * style.margin_l_ratio))
    margin_r = max(16, int(width * style.margin_r_ratio))
    margin_v = max(18, int(height * style.margin_v_ratio))
    alignment = ALIGNMENTS[position]
    box_color = _ass_color(style.back_color, opacity=style.background_opacity)
    outline_color = box_color if style.border_style == 3 else _ass_color(style.outline_color)
    style_line = ",".join(
        [
            "Default",
            style.font_name,
            str(font_size),
            _ass_color(style.primary_color),
            _ass_color(style.secondary_color),
            outline_color,
            box_color,
            "-1" if style.bold else "0",
            "-1" if style.italic else "0",
            "0",
            "0",
            "100",
            "100",
            f"{style.spacing:.2f}",
            "0",
            str(style.border_style),
            f"{style.outline:.2f}",
            f"{style.shadow:.2f}",
            str(alignment),
            str(margin_l),
            str(margin_r),
            str(margin_v),
            "1",
        ]
    )
    event_lines = [
        (
            "Dialogue: 0,"
            f"{_format_ass_timestamp(float(event['start']))},"
            f"{_format_ass_timestamp(float(event['end']))},"
            "Default,,0,0,0,,"
            f"{_event_prefix(style)}{event['text']}"
        )
        for event in events
    ]
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "ScaledBorderAndShadow: yes",
            "Collisions: Normal",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            (
                "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
                "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
                "Alignment,MarginL,MarginR,MarginV,Encoding"
            ),
            f"Style: {style_line}",
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
            *event_lines,
            "",
        ]
    )


def _font_size(style: SubtitleStyle, *, width: int, height: int) -> int:
    resolved = int(round(min(width, height) * style.font_size_scale))
    return max(int(style.min_font_size), min(resolved, int(style.max_font_size)))


def _ass_color(value: str, *, opacity: float = 1.0) -> str:
    raw = NAMED_COLORS.get(str(value or "").strip().lower(), str(value or "#FFFFFF").strip())
    if raw.startswith("&H"):
        return raw
    cleaned = raw.lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(char * 2 for char in cleaned)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        cleaned = "FFFFFF"
    red = cleaned[0:2]
    green = cleaned[2:4]
    blue = cleaned[4:6]
    alpha = int(round(255 * (1.0 - max(0.0, min(float(opacity), 1.0)))))
    return f"&H{alpha:02X}{blue.upper()}{green.upper()}{red.upper()}"


def _ass_inline_color(value: str) -> str:
    """Return an ASS inline primary-colour override value (&HBBGGRR&, no alpha)."""
    raw = NAMED_COLORS.get(str(value or "").strip().lower(), str(value or "#FFFFFF").strip())
    cleaned = raw.lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(char * 2 for char in cleaned)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        cleaned = "FFFFFF"
    red = cleaned[0:2]
    green = cleaned[2:4]
    blue = cleaned[4:6]
    return f"&H{blue.upper()}{green.upper()}{red.upper()}&"


def _format_ass_timestamp(seconds: float) -> str:
    centiseconds = int(round(max(float(seconds), 0.0) * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def _event_prefix(style: SubtitleStyle) -> str:
    if style.fade_in_ms <= 0 and style.fade_out_ms <= 0:
        return ""
    return f"{{\\fad({max(int(style.fade_in_ms), 0)},{max(int(style.fade_out_ms), 0)})}}"
