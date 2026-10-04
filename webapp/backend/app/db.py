"""SQLite storage: projects, cached AI answers and daily AI usage.

One small database file (DATA_DIR/reposage.db). Each project's files and
analysis results live next to it in DATA_DIR/projects/<id>/.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,          -- paste | files | zip | github | sample
    source_label TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL,
    status      TEXT NOT NULL,          -- working | ready | failed
    stage       TEXT NOT NULL DEFAULT '',
    progress    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    ai_status   TEXT NOT NULL DEFAULT 'waiting',  -- waiting | thinking | done | resting | off
    stats       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ai_cache (
    key         TEXT PRIMARY KEY,       -- hash of provider-independent request
    project_id  TEXT,
    kind        TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    result      TEXT NOT NULL,
    created     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
    day         TEXT NOT NULL,
    provider    TEXT NOT NULL,
    requests    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider)
);
"""


def db_path() -> str:
    return os.path.join(config.DATA_DIR, "reposage.db")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A short-lived connection; writes are serialised with a lock."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(db_path(), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---- projects -------------------------------------------------------------

def create_project(pid: str, name: str, source: str, label: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO projects (id, name, source, source_label, created, status, stage)"
                  " VALUES (?, ?, ?, ?, ?, 'working', 'unpacking')",
                  (pid, name, source, label, time.time()))


def update_project(pid: str, **fields: Any) -> None:
    if "stats" in fields and not isinstance(fields["stats"], str):
        fields["stats"] = json.dumps(fields["stats"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), pid))


def get_project(pid: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["stats"] = json.loads(out["stats"] or "{}")
    return out


def old_project_ids(keep: int) -> list[str]:
    with connect() as c:
        rows = c.execute("SELECT id FROM projects ORDER BY created DESC LIMIT -1 OFFSET ?",
                         (keep,)).fetchall()
    return [r["id"] for r in rows]


def delete_project(pid: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM projects WHERE id = ?", (pid,))


# ---- AI cache and usage -----------------------------------------------------

def cache_get(key: str) -> Optional[str]:
    with connect() as c:
        row = c.execute("SELECT result FROM ai_cache WHERE key = ?", (key,)).fetchone()
    return row["result"] if row else None


def cache_put(key: str, project_id: Optional[str], kind: str, provider: str,
              model: str, result: str) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO ai_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (key, project_id, kind, provider, model, result, time.time()))


def usage_today(provider: str) -> int:
    with connect() as c:
        row = c.execute("SELECT requests FROM ai_usage WHERE day = ? AND provider = ?",
                        (_today(), provider)).fetchone()
    return row["requests"] if row else 0


def count_request(provider: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO ai_usage (day, provider, requests) VALUES (?, ?, 1) "
                  "ON CONFLICT(day, provider) DO UPDATE SET requests = requests + 1",
                  (_today(), provider))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())
