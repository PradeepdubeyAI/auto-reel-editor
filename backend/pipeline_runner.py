#!/usr/bin/env python3
"""Runs the Reel Studio pipeline as a SUBPROCESS — executed with the VEX venv Python
(the only interpreter that can import the pipeline). The UI backend spawns this and
relays its stdout (one NDJSON object per line) to the browser over WebSocket.

Modes:
  recaption  — fast captions-only path  -> pipeline.recaption_only  (Captions -> Export -> QC)
  regenerate — full pipeline            -> pipeline.run_pipeline    (transcribe ... -> Export -> QC)

Usage:  <vex-venv-python> pipeline_runner.py <recaption|regenerate> <payload.json>
"""
import json
import re
import shutil
import sys
import traceback
from pathlib import Path

REEL_STUDIO = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(REEL_STUDIO))


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_recaption(payload: dict) -> None:
    import pipeline

    # recaption_only already logs NDJSON strings; forward each verbatim.
    def log(line) -> None:
        s = str(line)
        sys.stdout.write(s + ("" if s.endswith("\n") else "\n"))
        sys.stdout.flush()

    pipeline.recaption_only(
        payload["caption_base_video"],
        payload["transcript_json_path"],
        payload["output_path"],
        caption_engine=payload.get("caption_engine", "remotion"),
        bottom_percent=payload.get("bottom_percent", 22),
        style=payload.get("style", "word-focus"),
        fps=payload.get("fps", 60),
        run_qc=payload.get("run_qc", True),
        log=log,
    )


# run_pipeline emits free-text notes; map recognizable substrings to canonical steps.
STEP_MAP = [
    ("cleaning source audio", "Clean audio"),
    ("transcrib", "Transcribe"),
    ("romaniz", "Romanize"),
    ("strongest opening", "Trim to hook"),
    ("trimming silence", "Remove silences"),
    ("silent gap", "Remove silences"),
    ("color grade", "Color grade"),
    ("grading", "Color grade"),
    ("upscaling", "Captions"),
    ("caption", "Captions"),
    ("pro export", "Export"),
]
# greedy label so `detail` captures the LAST (...) group (QC's loudness line has nested parens)
QC_RE = re.compile(r"\[(PASS|FAIL)\]\s+(.*)\s+\(([^)]*)\)\s*$")


def run_regenerate(payload: dict) -> None:
    import pipeline

    recipe = payload.get("recipe", {})
    qc_checks: list[dict] = []

    def log(note) -> None:
        text = str(note)
        emit({"event": "log", "text": text})
        low = text.lower()
        for kw, name in STEP_MAP:
            if kw in low:
                emit({"event": "step", "name": name, "status": "running"})
                break
        m = QC_RE.search(text)
        if m:
            qc_checks.append({"label": m.group(2), "pass": m.group(1) == "PASS", "detail": m.group(3)})

    final, _ = pipeline.run_pipeline(
        payload["source_video"],
        caption_engine=payload.get("caption_engine", "remotion"),
        clean_audio=recipe.get("cleanAudio", False),
        trim_hook=recipe.get("trimHook", False),
        remove_silences=recipe.get("removeSilences", True),
        remove_retakes=recipe.get("removeRetakes", True),
        color_grade=recipe.get("colorGrade", True),
        color_grade_look=recipe.get("colorGradeLook", "natural"),
        color_grade_intensity=float(recipe.get("colorGradeIntensity", 0.5)),
        captions=True,
        pro_export=True,
        run_qc=True,
        log=log,
    )
    emit({"event": "done", "output": final, "qc": qc_checks})


INGEST_STEP_MAP = [
    ("cleaning source audio", "Clean audio"),
    ("transcrib", "Transcribe"),
    ("romaniz", "Romanize"),
    ("strongest opening", "Trim to hook"),
    ("trimming silence", "Remove silences"),
    ("silent gap", "Remove silences"),
    ("color grade", "Color grade"),
    ("grading", "Color grade"),
    ("upscaling", "Upscale"),
]


def run_broll_render(payload: dict) -> None:
    """B3: bake accepted B-roll + captions into the exported MP4 (approach A). render_broll
    already emits NDJSON (step/log/done) — forward each line verbatim, like run_recaption."""
    import render_broll

    def log(line) -> None:
        s = str(line)
        sys.stdout.write(s + ("" if s.endswith("\n") else "\n"))
        sys.stdout.flush()

    render_broll.render_broll_reel(
        payload["base_video"],
        payload["transcript_json_path"],
        payload["output_path"],
        payload["accepted_broll"],
        zooms=payload.get("zooms"),
        bottom_percent=payload.get("bottom_percent", 22),
        style=payload.get("style", "word-focus"),
        captions_off=payload.get("captions_off", False),
        fps=payload.get("fps", 60),
        run_qc=payload.get("run_qc", True),
        log=log,
    )


def run_broll_render_ffmpeg(payload: dict) -> None:
    """Remotion-FREE bake (pure ffmpeg + Pillow) — the MOBILE path. Same NDJSON contract."""
    from mobile_render import render_broll_ffmpeg

    def log(line) -> None:
        s = str(line)
        sys.stdout.write(s + ("" if s.endswith("\n") else "\n"))
        sys.stdout.flush()

    render_broll_ffmpeg.render_broll_ffmpeg_reel(
        payload["base_video"],
        payload["transcript_json_path"],
        payload["output_path"],
        payload["accepted_broll"],
        zooms=payload.get("zooms"),
        bottom_percent=payload.get("bottom_percent", 22),
        style=payload.get("style", "word-focus"),
        size_px=payload.get("size_px"),
        captions_off=payload.get("captions_off", False),
        fps=payload.get("fps", 60),
        run_qc=payload.get("run_qc", True),
        smooth_transitions=payload.get("smoothTransitions", True),
        sfx_hits=payload.get("sfxHits"),
        music=payload.get("music"),
        log=log,
    )


def run_ingest(payload: dict) -> None:
    """Front-half of the pipeline for a NEWLY UPLOADED clip: transcribe -> romanize ->
    grade -> upscale, captions OFF. Reuses run_pipeline unchanged; the un-captioned
    graded+upscaled `final` is the editor's base video, and the transcript is lifted from
    the Vex project's segments.json and converted with pipeline._build_whisper_json."""
    import pipeline

    projects_dir = Path.home() / ".video-agent" / "projects"
    before = {p.name for p in projects_dir.glob("*")} if projects_dir.exists() else set()
    recipe = payload.get("recipe", {})

    def log(note) -> None:
        text = str(note)
        emit({"event": "log", "text": text})
        low = text.lower()
        for kw, name in INGEST_STEP_MAP:
            if kw in low:
                emit({"event": "step", "name": name, "status": "running"})
                break

    captured: dict = {}

    def _stash_pregrade(path: str) -> None:
        # Copy OUT of vex's internal project dir into this backend's own managed uploads dir
        # (same TTL-swept lifecycle as everything else it manages), so a later "apply a
        # different look" action doesn't depend on vex's internal directory layout at all.
        try:
            dest = Path(payload["output_transcript"]).with_suffix("").with_suffix(".pregrade.mp4")
            shutil.copy2(path, dest)
            captured["pregrade"] = str(dest)
        except Exception:
            pass

    final, _ = pipeline.run_pipeline(
        payload["source_video"],
        caption_engine="pycaps",  # irrelevant: captions are OFF
        clean_audio=recipe.get("cleanAudio", False),
        trim_hook=recipe.get("trimHook", False),
        remove_silences=recipe.get("removeSilences", True),
        remove_retakes=recipe.get("removeRetakes", True),
        color_grade=recipe.get("colorGrade", True),
        color_grade_look=recipe.get("colorGradeLook", "natural"),
        color_grade_intensity=float(recipe.get("colorGradeIntensity", 0.5)),
        captions=False,     # front half only
        export_1080=True,   # graded + upscaled base (1080x1920)
        pro_export=False,   # no loudnorm (that's the back half)
        run_qc=False,
        on_project=lambda wd: captured.__setitem__("wd", wd),  # learn OUR project dir
        on_pregrade=_stash_pregrade,
        log=log,
    )
    if final is None:
        # run_pipeline returns None on a hard failure (e.g. the transcription API was
        # unreachable) -- previously this fell through to a "done" event with output=None,
        # which _activate_ingest happily wrote into projects[pid]["video"], so EVERY later
        # call for this project (fetch, plan, render) crashed on Path(None) instead of the
        # user ever seeing an error for the upload that actually failed.
        emit({"event": "error", "message": "processing failed — see the log above for the cause"})
        return

    # Read THIS run's transcript from the project dir the pipeline reported — NOT a racy
    # newest-file scan of the shared projects dir (two concurrent ingests would cross).
    seg_path = None
    wd = captured.get("wd")
    if wd and (Path(wd) / "transcript.segments.json").exists():
        seg_path = Path(wd) / "transcript.segments.json"
    else:
        # fallback for an older pipeline without on_project: legacy glob-diff (best-effort)
        after = list(projects_dir.glob("*")) if projects_dir.exists() else []
        cands = [d / "transcript.segments.json" for d in after if d.is_dir() and d.name not in before]
        cands = [p for p in cands if p.exists()] or list(projects_dir.glob("*/transcript.segments.json"))
        if cands:
            seg_path = max(cands, key=lambda p: p.stat().st_mtime)
    if seg_path is None:
        emit({"event": "error", "message": "ingest transcript (segments.json) not found"})
        return
    out_transcript = Path(payload["output_transcript"])
    segs = json.loads(seg_path.read_text())
    pipeline._build_whisper_json(segs, out_transcript)
    emit({
        "event": "done", "kind": "ingest", "output": final, "transcript": str(out_transcript),
        "pregrade": captured.get("pregrade"),  # None if the checkpoint copy failed — regrade
        # then just isn't offered for this project rather than erroring later.
        # This run's own project dir -- retake-cut checkpoint/ledger (if any) live here, read by
        # GET /api/retakes and POST /api/retakes/restore. None if remove_silences was off or the
        # pipeline predates this field, same "just isn't offered" fallback as pregrade above.
        "retakesDir": captured.get("wd"),
    })


def run_regrade(payload: dict) -> None:
    """Re-grade an already-ingested project's cached PRE-grade checkpoint with a new
    look/intensity — the Fine-tune "Look" picker's Apply action. Does NOT re-transcribe."""
    import pipeline

    def log(note) -> None:
        text = str(note)
        emit({"event": "log", "text": text})
        low = text.lower()
        if "applying" in low:
            emit({"event": "step", "name": "Grade", "status": "running"})
        elif "upscaling" in low:
            emit({"event": "step", "name": "Upscale", "status": "running"})

    out = pipeline.regrade(
        payload["pregrade_video"],
        payload["output_path"],
        look=payload.get("look", "natural"),
        intensity=float(payload.get("intensity", 0.5)),
        log=log,
    )
    emit({"event": "step", "name": "Grade", "status": "done"})
    emit({"event": "step", "name": "Upscale", "status": "done"})
    emit({"event": "done", "kind": "regrade", "output": out})


def run_restore_retakes(payload: dict) -> None:
    """Fine-tune "Trimmed" sheet's restore action: re-apply the retake-cut ledger against its
    checkpoint with the user's chosen ids excluded. Does NOT re-transcribe or touch silence-trim
    -- only which retakes are currently cut changes."""
    import pipeline

    def log(note) -> None:
        emit({"event": "log", "text": str(note)})

    emit({"event": "step", "name": "Restore", "status": "running"})
    out = pipeline.restore_retakes(
        payload["working_dir"],
        payload["output_path"],
        keep_ids=payload.get("keep_ids") or [],
        log=log,
    )
    emit({"event": "step", "name": "Restore", "status": "done"})
    # Rebuild the render's transcript from the timeline restore_retakes just produced. It rewrites
    # transcript.segments.json with remapped word times, but the file the CAPTION render reads is the
    # project's whisper.json, written once at ingest -- so without this the video gets shorter while
    # the captions keep the pre-trim timings. Every cut applied here shifted the caption track
    # against the audio from that cut onward, worsening down the timeline: the speaker says one thing
    # and the caption shows what used to be at that timestamp.
    new_transcript = None
    try:
        seg_path = Path(payload["working_dir"]) / "transcript.segments.json"
        want = payload.get("output_transcript")
        if seg_path.exists() and want:
            pipeline._build_whisper_json(json.loads(seg_path.read_text()), Path(want))
            new_transcript = str(want)
    except Exception as e:   # captions staying stale is bad, losing the re-cut video is worse
        emit({"event": "log", "text": f"could not rebuild caption transcript: {e}"})
    emit({"event": "done", "kind": "restore_retakes", "output": out,
          "transcript": new_transcript})


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.loads(Path(sys.argv[2]).read_text())
    try:
        if mode == "recaption":
            run_recaption(payload)
        elif mode == "regenerate":
            run_regenerate(payload)
        elif mode == "ingest":
            run_ingest(payload)
        elif mode == "broll_render":
            run_broll_render(payload)
        elif mode == "broll_render_ffmpeg":
            run_broll_render_ffmpeg(payload)
        elif mode == "regrade":
            run_regrade(payload)
        elif mode == "restore_retakes":
            run_restore_retakes(payload)
        else:
            emit({"event": "error", "message": f"unknown mode {mode!r}"})
    except Exception as e:  # noqa: BLE001 - surface any failure to the client
        emit({"event": "error", "message": str(e), "trace": traceback.format_exc()[-1000:]})


if __name__ == "__main__":
    main()
