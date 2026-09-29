"""Command-line entry point:  python -m reposage scan [PATH] [--full]

The /reposage:scan skill calls this through bootstrap.py, which makes
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
#   ai/         Work files of /reposage:summarize (they contain copies of your code).
#   dashboard.html  Generated page (/reposage:dashboard); rebuilt on demand.
#
# To keep everything private instead, replace the lines below with:  *
cache/
ai/
dashboard.html
*.tmp
"""


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reposage",
                                 description="Build a knowledge graph of a codebase.")
    ap.add_argument("--version", action="version", version=f"reposage {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan a repo and write .reposage/graph.json")
    scan.add_argument("path", nargs="?", default=".", help="repo folder (default: .)")
    scan.add_argument("--full", action="store_true",
                      help="ignore the cache and re-parse every file")
    dash = sub.add_parser("dashboard",
                          help="scan, then write and open .reposage/dashboard.html")
    dash.add_argument("path", nargs="?", default=".", help="repo folder (default: .)")
    dash.add_argument("--no-open", action="store_true",
                      help="only write the file, don't open a browser")
    summ = sub.add_parser("summarize", help="AI summaries: plan batches, save answers")
    ssub = summ.add_subparsers(dest="action", required=True)
    sp = ssub.add_parser("plan", help="scan, choose files, write batches, show the estimate")
    sp.add_argument("path", nargs="?", default=".")
    sp.add_argument("--include-tests", action="store_true", help="summarize test files too")
    sp.add_argument("--force", action="store_true", help="summarize again even if up to date")
    sp.add_argument("--limit", type=int, help="only the N most connected files")
    sp.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)  # used by the skill, not here
    sr = ssub.add_parser("run", help="send the planned batches to Claude (Haiku) and save the answers")
    sr.add_argument("path", nargs="?", default=".")
    sr.add_argument("--model", default="haiku", help="Claude model for the summaries (default: haiku)")
    sr.add_argument("--parallel", type=int, default=4, help="batches at the same time (default: 4)")
    ss = ssub.add_parser("save", help="check one batch's JSON answer and merge it into the graph")
    ss.add_argument("batch", type=int)
    ss.add_argument("path", nargs="?", default=".")
    ss.add_argument("--file", help="answer file (default: .reposage/ai/answer-NNN.json; '-' = stdin)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.command == "scan":
        return run_scan(os.path.abspath(args.path), full=args.full)
    if args.command == "summarize":
        return run_summarize(args)
    if args.command == "dashboard":
        return run_dashboard(os.path.abspath(args.path), open_browser=not args.no_open)
    return 1


def run_summarize(args) -> int:
    from . import summarize

    repo = os.path.abspath(args.path)
    if args.action == "plan":
        code = run_scan(repo)
        if code != 0:
            return code
        print()
        graph = _load_json(os.path.join(repo, OUT_DIR, "graph.json"))
        summarize.print_plan(summarize.plan(repo, graph, include_tests=args.include_tests,
                                            force=args.force, limit=args.limit))
        return 0

    if args.action == "run":
        if not os.path.exists(os.path.join(repo, OUT_DIR, "ai", "plan.json")):
            print("RepoSage: no summarize plan found; run `summarize plan` first.", file=sys.stderr)
            return 2
        try:
            totals = summarize.run(repo, model=args.model, parallel=args.parallel,
                                   say=lambda m: print(m, flush=True))
        except RuntimeError as e:
            print(f"RepoSage: {e}", file=sys.stderr)
            return 2
        return 1 if totals["failed"] else 0

    # save: an answer written to .reposage/ai/answer-NNN.json (or stdin)
    if not os.path.exists(os.path.join(repo, OUT_DIR, "ai", "plan.json")):
        print("RepoSage: no summarize plan found; run `summarize plan` first.", file=sys.stderr)
        return 2
    answer_path = args.file or os.path.join(repo, OUT_DIR, "ai", f"answer-{args.batch:03d}.json")
    if answer_path == "-":
        answer = sys.stdin.read()
    else:
        try:
            with open(answer_path, encoding="utf-8") as fh:
                answer = fh.read()
        except OSError:
            print(f"Batch {args.batch}: no answer file at {answer_path}", file=sys.stderr)
            return 1
    files, symbols, problems = summarize.save(repo, args.batch, answer)
    done, total = summarize.progress(repo)
    print(f"Batch {args.batch}: saved {files} file summaries and {symbols} function/class summaries.")
    for p in problems:
        print(f"  problem: {p}")
    print(f"Progress: {done} of {total} planned files summarized.")
    return 1 if files == 0 else 0


def run_dashboard(repo: str, open_browser: bool = True) -> int:
    """Refresh the graph (incremental, so usually instant), then build the page."""
    from .dashboard.build import write_dashboard   # only needed here

    code = run_scan(repo)
    if code != 0:
        return code
    graph = _load_json(os.path.join(repo, OUT_DIR, "graph.json"))
    out = os.path.join(repo, OUT_DIR, "dashboard.html")
    data = write_dashboard(graph, repo, out)

    url = "file://" + ("" if out.startswith("/") else "/") + out.replace(os.sep, "/")
    opened = False
    if open_browser:
        import webbrowser
        try:
            opened = webbrowser.open(url)
        except Exception:  # no browser available (e.g. a remote server)
            opened = False
    print()
    print(f"RepoSage dashboard: {os.path.relpath(out, repo)} "
          f"({os.path.getsize(out) / 1024:.0f} KB, {len(data['files']['path'])} files, "
          f"{len(data['sym']['name'])} symbols)")
    print(f"  {'Opened in your browser' if opened else 'Open this in a browser'}: {url}")
    return 0


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
