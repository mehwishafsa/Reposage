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
    dash.add_argument("--answer", metavar="ID",
                      help="open with a saved Answer Path shown ('latest' for the newest)")
    dash.add_argument("--out", help="write the page here instead of .reposage/dashboard.html")
    dash.add_argument("--public", action="store_true",
                      help="for publishing: leave out local folder paths (no 'Open in VS Code')")
    dash.add_argument("--no-open", action="store_true",
                      help="only write the file, don't open a browser")
    chat = sub.add_parser("chat", help="questions about the code, and Answer Paths")
    csub = chat.add_subparsers(dest="action", required=True)
    ca = csub.add_parser("ask", help="find the code for a question and print a context pack")
    ca.add_argument("question", nargs="+")
    ca.add_argument("--repo", default=".", help="repo folder (default: .)")
    ca.add_argument("--include-tests", action="store_true", help="search test files too")
    cp = csub.add_parser("path", help="save the steps of an answer and show them on the dashboard")
    cp.add_argument("steps", nargs="+", help='"N:short label" per step, in order')
    cp.add_argument("--answer", default="", help="the answer in one or two sentences")
    cp.add_argument("--repo", default=".", help="repo folder (default: .)")
    cp.add_argument("--no-open", action="store_true", help="don't open a browser")
    df = sub.add_parser("diff", help="what do the current git changes affect?")
    df.add_argument("--repo", default=".", help="repo folder (default: .)")
    df.add_argument("--base", help="compare with where this branch left BASE (e.g. main); "
                                   "default: uncommitted changes")
    df.add_argument("--depth", type=int, default=3, help="how many levels of callers (default: 3)")
    df.add_argument("--note", help="add a plain-English explanation to the latest change view")
    df.add_argument("--no-open", action="store_true", help="don't open a browser")
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
        return run_dashboard(os.path.abspath(args.path), open_browser=not args.no_open,
                             answer=args.answer, out=args.out, public=args.public)
    if args.command == "chat":
        return run_chat(args)
    if args.command == "diff":
        return run_diff(args)
    return 1


def run_summarize(args) -> int:
    from . import summarize

    repo = os.path.abspath(args.path)
    if args.action == "plan":
        code = run_scan(repo)
        if code != 0:
            return code
        print()
        warn_if_ai_not_ignored(repo)
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


def run_chat(args) -> int:
    import contextlib
    import io
    from . import chat

    repo = os.path.abspath(args.repo)
    if args.action == "ask":
        with contextlib.redirect_stdout(io.StringIO()):   # keep the output for Claude short
            code = run_scan(repo)
        if code != 0:
            return code
        graph = _load_json(os.path.join(repo, OUT_DIR, "graph.json"))
        print(chat.ask(repo, graph, " ".join(args.question), include_tests=args.include_tests))
        return 0

    try:
        record = chat.save_path(repo, args.steps, args.answer)
    except (OSError, ValueError) as e:
        print(f"RepoSage: could not save the Answer Path: {e}", file=sys.stderr)
        return 2
    print(f"Answer Path saved: {len(record['steps'])} steps, confidence {record['confidence']}.")
    print(f"  {record['confidence_note']}")
    for p in record["problems"]:
        print(f"  problem: {p}")
    with contextlib.redirect_stdout(io.StringIO()):
        run_dashboard(repo, open_browser=False)
    return open_dashboard(repo, not args.no_open, record["id"])


def run_diff(args) -> int:
    import contextlib
    import io
    from . import diff

    repo = os.path.abspath(args.repo)
    if args.note:
        record = diff.add_summary(repo, args.note)
        if record is None:
            print("RepoSage: no change view yet; run `diff` first.", file=sys.stderr)
            return 1
        with contextlib.redirect_stdout(io.StringIO()):
            run_dashboard(repo, open_browser=False)
        print("Explanation added to the change view.")
        return open_dashboard(repo, not args.no_open, record["id"])

    with contextlib.redirect_stdout(io.StringIO()):       # keep the output for Claude short
        code = run_scan(repo)
    if code != 0:
        return code
    graph = _load_json(os.path.join(repo, OUT_DIR, "graph.json"))
    try:
        changes = diff.read_changes(repo, args.base)
    except diff.DiffError as e:
        print(f"RepoSage: {e}", file=sys.stderr)
        return 2
    if not changes["files"]:
        print(f"No {changes['label']} found.")
        return 0
    analysis = diff.analyze(repo, graph, changes, max_depth=args.depth)
    print(diff.render_pack(repo, analysis))
    record = diff.save_view(repo, analysis)
    with contextlib.redirect_stdout(io.StringIO()):
        run_dashboard(repo, open_browser=False)
    print()
    print(f"Change view saved (risk {record['risk']}).")
    return open_dashboard(repo, not args.no_open, record["id"])


def open_dashboard(repo: str, open_browser: bool, answer: str | None = None) -> int:
    """Print (and try to open) the dashboard link, optionally at an Answer Path."""
    out = os.path.join(repo, OUT_DIR, "dashboard.html")
    url = "file://" + ("" if out.startswith("/") else "/") + out.replace(os.sep, "/")
    if answer:
        if answer == "latest":
            from .chat import load_answers
            latest = load_answers(repo, limit=1)
            if not latest:
                print("RepoSage: no saved Answer Paths yet; ask with /reposage:chat first.")
                return 1
            answer = latest[0]["id"]
        url += "#answer=" + answer
    opened = False
    if open_browser:
        import webbrowser
        try:
            opened = webbrowser.open(url)
        except Exception:  # no browser available (e.g. a remote server)
            opened = False
    print(f"  {'Opened in your browser' if opened else 'Open this in a browser'}: {url}")
    if answer:
        print(f"  Reopen later:  python3 <plugin>/reposage/bootstrap.py dashboard --answer {answer}")
    return 0


def run_dashboard(repo: str, open_browser: bool = True, answer: str | None = None,
                  out: str | None = None, public: bool = False) -> int:
    """Refresh the graph (incremental, so usually instant), then build the page."""
    from .dashboard.build import write_dashboard   # only needed here

    code = run_scan(repo)
    if code != 0:
        return code
    graph = _load_json(os.path.join(repo, OUT_DIR, "graph.json"))
    from .layers import load_overrides
    for problem in load_overrides(repo)[1]:
        print(f"WARNING: {problem}")
    if out:
        # Written somewhere else (e.g. for GitHub Pages): print where, don't open.
        data = write_dashboard(graph, repo, os.path.abspath(out), public=public)
        print(f"RepoSage dashboard written to {out} ({os.path.getsize(out) / 1024:.0f} KB, "
              f"{len(data.get('answers', []))} saved answers/changes)")
        return 0
    out = os.path.join(repo, OUT_DIR, "dashboard.html")
    data = write_dashboard(graph, repo, out, public=public)

    print()
    print(f"RepoSage dashboard: {os.path.relpath(out, repo)} "
          f"({os.path.getsize(out) / 1024:.0f} KB, {len(data['files']['path'])} files, "
          f"{len(data['sym']['name'])} symbols)")
    return open_dashboard(repo, open_browser, answer)


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


def warn_if_ai_not_ignored(repo: str) -> bool:
    """.reposage/ai/ holds copies of source code sent to the AI. Projects set
    up before it existed may not ignore it; we never edit the user's file,
    but we say so clearly. Returns True if a warning was printed."""
    import subprocess
    probe = f"{OUT_DIR}/ai/plan.json"
    try:
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo,
                                capture_output=True, text=True, timeout=20).stdout.strip() == "true"
        if not inside:
            return False
        ignored = subprocess.run(["git", "check-ignore", "-q", "--no-index", probe], cwd=repo,
                                 capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    if ignored:
        return False
    print("WARNING: .reposage/ai/ is not git-ignored in this project. It holds copies of")
    print("         your code that are sent to the AI, so it should not be committed.")
    print("         One-line fix:  echo \"ai/\" >> .reposage/.gitignore")
    print()
    return True


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
