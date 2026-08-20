"""Project-directory setup, inlined from Vex's main.create_project().

The original lives in vex/main.py, a 111K module that also pulls in the agent
loop, plugin system and NL intent compiler -- none of which this pipeline uses.
create_project() itself is ~25 lines of "copy the source into an isolated
working directory and save a ProjectState", so it's reproduced directly here
instead of vendoring the rest of main.py's import graph.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import config
from engine import probe_video
from state import ProjectState, utc_now_iso


def create_project(video_path: str, name: str | None, provider_name: str, model_name: str) -> ProjectState:
    absolute_path = os.path.abspath(video_path)
    project_id = str(uuid.uuid4())
    project_name = name or Path(video_path).stem
    working_dir = Path(config.AGENT_PROJECTS_DIR) / project_id
    working_dir.mkdir(parents=True, exist_ok=True)
    working_file = str(working_dir / f"source_{Path(absolute_path).name}")
    shutil.copy2(absolute_path, working_file)
    metadata = probe_video(working_file)
    state = ProjectState(
        project_id=project_id,
        project_name=project_name,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        source_files=[absolute_path],
        working_file=working_file,
        working_dir=str(working_dir),
        output_dir=str(Path(absolute_path).parent),
        timeline=[],
        redo_stack=[],
        session_log=[],
        metadata=metadata,
        provider=provider_name,
        model=model_name,
    )
    state.save()
    return state
