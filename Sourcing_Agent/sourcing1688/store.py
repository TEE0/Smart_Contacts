"""Small JSON store for the preference profile and saved shortlists.

Deliberately file-based: the agent must keep working when it is launched as a
stdio subprocess with no database, and the buyer should be able to read and edit
their own profile with a text editor.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import home_dir


def _path(name: str) -> Path:
    return home_dir() / name


def read_json(name: str, default: Any = None) -> Any:
    path = _path(name)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(name: str, payload: Any) -> Path:
    """Atomic write - a half-written profile is worse than a stale one."""
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SHORTLIST_FILE = "shortlists.json"


def load_shortlists() -> dict[str, Any]:
    return read_json(SHORTLIST_FILE, default={}) or {}


def save_shortlist(name: str, entries: list[dict[str, Any]], brief: str = "") -> dict[str, Any]:
    data = load_shortlists()
    record = {
        "name": name,
        "brief": brief,
        "saved_at": now_iso(),
        "count": len(entries),
        "entries": entries,
    }
    data[name] = record
    write_json(SHORTLIST_FILE, data)
    return record


def delete_shortlist(name: str) -> bool:
    data = load_shortlists()
    if name not in data:
        return False
    del data[name]
    write_json(SHORTLIST_FILE, data)
    return True
