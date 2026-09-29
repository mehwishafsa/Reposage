"""Command-line entry point:  python -m reposage scan [PATH] [--full]

The /reposage slash command calls this through bootstrap.py, which makes
sure the private virtual environment (with Tree-sitter) exists first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .cache import FactsCache, write_json_atomic
from .graph_builder import build_graph
from .parsers import parser_for, supported_extensions
from .parsers.base import FileFacts
from .scanner import file_hash, list_source_files

OUT_DIR = ".reposage"

# Written once into .reposage/.gitignore. graph.json is NOT ignored, so a
# team can commit it as a shared map of the codebase if they want to.
GITIGNORE = """\
# RepoSage output folder.
#
#   graph.json  The knowledge graph. Commit it if you want your team to share
#               one map of the codebase; it only changes when code changes.
#   cache/      Local parse cache. Machine-specific, never commit it.
#
# To keep everything private instead, replace the lines below with:  *
cache/
*.tmp
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reposage",
                                 description="Build a knowledge graph of a codebase.")
    ap.add_argument("--version", action="version", version=f"reposage {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan a repo and write .reposage/graph.json")
    scan.add_argument("path", nargs="?", default=".", help="repo folder (default: .)")
    scan.add_argument("--full", action="store_true",
                      help="ignore the cache and re-parse every file")
    args = ap.parse_args(argv)

    if args.command == "scan":
        return run_scan(os.path.abspath(args.path), full=args.full)
    return 1


def run_scan(repo: str, full: bool = False) -> int:
    if not os.path.isdir(repo):
        print(f"RepoSage: not a folder: {repo}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    out_dir = os.path.join(repo, OUT_DIR)
    graph_path = os.path.join(out_dir, "graph.json")
    os.makedirs(out_dir, exist_ok=True)
    _ensure_gitignore(out_dir)

    cache = FactsCache(os.path.join(out_dir, "cache", "facts.json"))
    if full:
        cache.entries = {}
    previous = _load_json(graph_path)

    # 1. Find files and parse the ones that changed.
    files = list_source_files(repo, supported_extensions())
    facts_by_path: dict[str, FileFacts] = {}
    hashes: dict[str, str] = {}
    cache_out = {}
    parsed = reused = 0
    failed: list[str] = []

    for rel in files:
        parser = parser_for(rel)
        try:
            with open(os.path.join(repo, rel), "rb") as fh:
                data = fh.read()
        except OSError as e:
            failed.append(f"{rel} ({e.strerror})")
            continue
        digest = file_hash(data)
        facts = cache.get(rel, digest, parser.name, parser.version)
        if facts is None:
            try:
                facts = parser.parse(rel, data)
            except Exception as e:  # a bug in one parser must not stop the scan
                failed.append(f"{rel} ({type(e).__name__}: {e})")
                continue
            parsed += 1
        else:
            reused += 1
        facts_by_path[rel] = facts
        hashes[rel] = digest
        cache_out[rel] = (digest, parser.name, parser.version, facts)

    removed = len(set(cache.entries) - set(facts_by_path))

    # 2. Link everything into one graph and save it.
    graph = build_graph(facts_by_path, hashes, previous)
    write_json_atomic(graph_path, graph)
    cache.save(cache_out)

    # 3. Report.
    _print_report(repo, graph, graph_path, parsed, reused, removed, failed,
                  time.perf_counter() - started)
    return 0


def _print_report(repo, graph, graph_path, parsed, reused, removed, failed,
                  seconds) -> None:
    s = graph["stats"]
    langs = ", ".join(f"{k} {v}" for k, v in s["languages"].items()) or "none"
    n, e = s["nodes"], s["edges"]
    funcs = n.get("function", 0) + n.get("method", 0)
    with_errors = [x["path"] for x in graph["nodes"]
                   if x["type"] == "file" and x["has_errors"]]
    size_kb = os.path.getsize(graph_path) / 1024

    print(f"RepoSage scan complete: {repo}")
    print(f"  Files      {s['files']} ({langs})")
    print(f"  Parsed     {parsed} new/changed, {reused} unchanged (cached), {removed} removed")
    print(f"  Nodes      {n.get('file', 0)} files, {n.get('class', 0)} classes, "
          f"{n.get('interface', 0)} interfaces, {funcs} functions/methods")
    print(f"  Edges      {e.get('calls', 0)} calls, {e.get('imports', 0)} imports, "
          f"{e.get('contains', 0)} contains")
    print(f"  Output     {os.path.relpath(graph_path, repo)} ({size_kb:.0f} KB)")
    print(f"  Time       {seconds:.1f}s")
    if with_errors:
        print(f"  Note       {len(with_errors)} file(s) have syntax errors; "
              f"parsed what we could: {', '.join(with_errors[:5])}"
              + (" ..." if len(with_errors) > 5 else ""))
    if failed:
        print(f"  Skipped    {len(failed)} file(s) could not be read/parsed:")
        for item in failed[:10]:
            print(f"               {item}")
    if s["files"] == 0:
        print("  (No Python / JavaScript / TypeScript / Java files found.)")


def _ensure_gitignore(out_dir: str) -> None:
    """Create .reposage/.gitignore once; never overwrite the user's edits."""
    path = os.path.join(out_dir, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(GITIGNORE)


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
