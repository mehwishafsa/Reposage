"""Shared test helpers: copy a fixture project to a temp folder and scan it."""

import contextlib
import io
import json
import os
import shutil
import tempfile

from reposage.__main__ import run_scan

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def copy_fixture(name: str) -> str:
    """Copy tests/fixtures/<name> into a fresh temp dir and return its path."""
    dest = os.path.join(tempfile.mkdtemp(prefix="reposage-test-"), name)
    shutil.copytree(os.path.join(FIXTURES, name), dest)
    return dest


def scan(repo: str, full: bool = False) -> tuple[dict, str]:
    """Run a scan; return (graph dict, printed report)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = run_scan(repo, full=full)
    assert code == 0, out.getvalue()
    with open(os.path.join(repo, ".reposage", "graph.json"), encoding="utf-8") as f:
        return json.load(f), out.getvalue()


def node_ids(graph: dict, node_type: str | None = None) -> set[str]:
    return {n["id"] for n in graph["nodes"]
            if node_type is None or n["type"] == node_type}


def edges(graph: dict, edge_type: str) -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in graph["edges"] if e["type"] == edge_type}


def node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)
