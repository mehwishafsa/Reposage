"""Project lifecycle: create -> unpack -> analyse -> AI notes (background).

Each project gets an unguessable id and a folder DATA_DIR/projects/<id>/:
    src/            the uploaded code (only readable source files)
    graph.json      the code graph from the engine
    overview.json   the dashboard data (no AI)
    ai.json         AI notes, filled in the background
Only the newest MAX_PROJECTS_KEPT projects are kept on disk.
"""

from __future__ import annotations

import os
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .. import config, db
from . import ai_notes, analyze, ingest

# Two analyses at a time is plenty for a small server; AI notes have their
# own single-file queue inside llm.py.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyse")
_notes_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-notes")
_cleanup_lock = threading.Lock()


def project_dir(pid: str) -> str:
    return os.path.join(config.DATA_DIR, "projects", pid)


def new_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")


def valid_id(pid: str) -> bool:
    return pid.isalnum() and 6 <= len(pid) <= 32


def start(name: str, source: str, label: str,
          unpack: Callable[[str], ingest.IngestReport], run_async: bool = True) -> str:
    """Unpack now (so upload errors are reported at once), analyse in the background."""
    pid = new_id()
    folder = project_dir(pid)
    os.makedirs(folder)
    try:
        report = unpack(os.path.join(folder, "src"))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    db.create_project(pid, _clean_name(name), source, label[:300])
    job = lambda: _analyse(pid, name, report)       # noqa: E731
    if run_async:
        _pool.submit(job)
    else:
        job()
    _cleanup()
    return pid


def _analyse(pid: str, name: str, report: ingest.IngestReport) -> None:
    folder = project_dir(pid)
    try:
        overview = analyze.analyse(
            folder, _clean_name(name), {"kept": report.kept, "skipped": report.skipped},
            progress=lambda stage, pct: db.update_project(pid, stage=stage, progress=pct))
        db.update_project(pid, status="ready", stage="done", progress=100,
                          stats=overview["stats"], ai_status="waiting")
    except Exception as e:                       # report, never crash the server
        db.update_project(pid, status="failed", stage="failed",
                          error=f"Something went wrong while reading the code ({type(e).__name__}).")
        return
    _notes_pool.submit(run_notes, pid)


def run_notes(pid: str) -> str:
    folder = project_dir(pid)
    overview = analyze.load_json(os.path.join(folder, "overview.json"))
    if overview is None:
        return "off"
    status = ai_notes.write_notes(folder, overview,
                                  on_status=lambda s: db.update_project(pid, ai_status=s))
    db.update_project(pid, ai_status=status)
    return status


def retry_notes(pid: str) -> None:
    db.update_project(pid, ai_status="waiting")
    _notes_pool.submit(run_notes, pid)


def overview(pid: str) -> dict | None:
    folder = project_dir(pid)
    data = analyze.load_json(os.path.join(folder, "overview.json"))
    if data is None:
        return None
    return ai_notes.apply_notes(data, ai_notes.load_notes(folder))


def read_source(pid: str, rel: str) -> str | None:
    """Text of one uploaded file (only paths inside the project's src/)."""
    rel = ingest.safe_relpath(rel)
    if rel is None:
        return None
    base = os.path.realpath(os.path.join(project_dir(pid), "src"))
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return None
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _cleanup() -> None:
    with _cleanup_lock:
        for pid in db.old_project_ids(config.MAX_PROJECTS_KEPT):
            shutil.rmtree(project_dir(pid), ignore_errors=True)
            db.delete_project(pid)


def _clean_name(name: str) -> str:
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return (name or "my-project")[:60]
