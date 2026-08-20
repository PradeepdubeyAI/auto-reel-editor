"""Reel Studio UI — minimal persistence layer.

The rest of this backend deliberately has NO database: all job/project state lives in
in-memory dicts in `server.py` by design (single uvicorn worker, see its module docstring),
and that's correct for genuinely ephemeral, SSE-driven request state — it should stay that way.

This module exists for the narrow slice of data that actually needs to survive a backend
restart and that upcoming features (music/SFX libraries, a persistent user B-roll library,
"last used look" style preferences) all independently need: static reference/library rows
and small per-project preference blobs. It is intentionally NOT a general-purpose ORM or a
place to put job/render state — nothing here is written on a hot path.

One file, stdlib `sqlite3`, no ORM — matches this backend's existing zero-infra philosophy
(no Postgres, no SQLAlchemy, nothing else in this codebase pulls in a database dependency).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent / "reel_studio.db"

# sqlite3 connections aren't safe to share across threads by default; FastAPI's async routes
# still run sync DB calls on the event loop thread in this app (no run_in_executor anywhere
# yet), so a single lock is enough here — this is a low-traffic metadata store, not a hot path.
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # avoid readers blocking on the odd concurrent write
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call every process startup."""
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS asset_library (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,        -- e.g. 'music', 'sfx', 'broll'
                label TEXT NOT NULL,
                file_path TEXT NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_library_category ON asset_library(category)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                project_id TEXT NOT NULL,      -- '' for a global/device-wide preference
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (project_id, key)
            )
        """)


# ---- asset_library ----------------------------------------------------------------------

def add_asset(id: str, category: str, label: str, file_path: str, meta: Optional[Dict[str, Any]] = None) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO asset_library (id, category, label, file_path, meta_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, category, label, file_path, json.dumps(meta or {}), time.time()),
        )


def list_assets(category: str) -> List[Dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, category, label, file_path, meta_json, created_at FROM asset_library "
            "WHERE category = ? ORDER BY created_at ASC",
            (category,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "category": r["category"],
            "label": r["label"],
            "filePath": r["file_path"],
            "meta": json.loads(r["meta_json"]),
            "createdAt": r["created_at"],
        })
    return out


def remove_asset(id: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM asset_library WHERE id = ?", (id,))


# ---- preferences -------------------------------------------------------------------------

def set_pref(project_id: str, key: str, value: Any) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO preferences (project_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
            (project_id or "", key, json.dumps(value), time.time()),
        )


def get_pref(project_id: str, key: str, default: Any = None) -> Any:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value_json FROM preferences WHERE project_id = ? AND key = ?",
            (project_id or "", key),
        ).fetchone()
    return json.loads(row["value_json"]) if row else default
