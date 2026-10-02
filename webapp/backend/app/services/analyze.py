"""Analyse a project folder with the RepoSage engine and build the overview.

Everything here is DETERMINISTIC and needs no AI:
  - the code graph (files, functions, calls, imports) from Tree-sitter
  - languages, file list, "Start here" reading order, file map
  - a plain-English overview written from the code's own comments
AI notes (ai_notes.py) are added on top later and only improve the words.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Callable

from reposage.cache import write_json_atomic
from reposage.graph_builder import build_graph
from reposage.layers import guess_layer
from reposage.parsers import parser_for, supported_extensions
from reposage.scanner import file_hash, list_source_files

SYMBOLS = ("class", "interface", "enum", "record", "function", "method")
LANG_NAMES = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
              "java": "Java", "c": "C"}
ENTRY_NAMES = {"main", "app", "index", "cli", "__main__", "server", "program", "run", "start", "manage"}
ROLE_BY_LAYER = {"UI": "shows things on screen", "API": "handles requests",
                 "Service": "main logic", "Data": "stores data",
                 "Utility": "helpers", "Tests": "tests", "Unclassified": ""}


def build_project_graph(src: str, progress: Callable[[str, int], None] = lambda s, p: None) -> dict:
    """Parse every file and link them into one graph (same engine as the plugin)."""
    progress("reading", 30)
    files = list_source_files(src, supported_extensions())
    facts, hashes = {}, {}
    progress("finding", 50)
    for rel in files:
        with open(os.path.join(src, rel), "rb") as fh:
            data = fh.read()
        try:
            facts[rel] = parser_for(rel).parse(rel, data)
        except Exception:               # one odd file must not stop the analysis
            continue
        hashes[rel] = file_hash(data)
    progress("connecting", 70)
    return build_graph(facts, hashes)


def analyse(project_dir: str, name: str, ingest_report: dict,
            progress: Callable[[str, int], None] = lambda s, p: None) -> dict:
    src = os.path.join(project_dir, "src")
    graph = build_project_graph(src, progress)
    write_json_atomic(os.path.join(project_dir, "graph.json"), graph)
    progress("overview", 85)
    overview = build_overview(graph, src, name)
    overview["upload"] = {"kept": len(ingest_report.get("kept", [])),
                          "skipped": ingest_report.get("skipped", {})}
    write_json_atomic(os.path.join(project_dir, "overview.json"), overview)
    return overview


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------

def build_overview(graph: dict, src: str, name: str) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    file_nodes = [n for n in graph["nodes"] if n["type"] == "file"]
    symbols_in: dict[str, list[dict]] = defaultdict(list)
    for n in graph["nodes"]:
        if n["type"] in SYMBOLS:
            symbols_in[n["path"]].append(n)
    for lst in symbols_in.values():
        lst.sort(key=lambda n: (n["start_line"], n["id"]))

    lines = {f["id"]: _count_lines(os.path.join(src, f["id"])) for f in file_nodes}
    links = _file_links(graph)
    outgoing, incoming = defaultdict(int), defaultdict(int)
    for (s, t), v in links.items():
        outgoing[s] += v["calls"] + v["imports"]
        incoming[t] += v["calls"] + v["imports"]

    entry = pick_entry(file_nodes, symbols_in, src, outgoing, incoming)
    files = []
    for f in sorted(file_nodes, key=lambda n: n["id"]):
        path = f["id"]
        funcs = symbols_in[path]
        layer = guess_layer(path)
        files.append({
            "path": path,
            "language": f.get("language", ""),
            "lines": lines[path],
            "summary": summary_from_code(path, f.get("doc", ""), funcs),
            "summary_source": "code",
            "role": "starts the program" if path == entry else
                    ("header: lists functions" if path.endswith(".h") else
                     ROLE_BY_LAYER.get(layer, "") or ("used by other files" if incoming[path] else "")),
            "connections": outgoing[path] + incoming[path],
            "has_errors": bool(f.get("has_errors")),
            "functions": [{"id": s["id"], "name": s["name"], "kind": s["type"],
                           "line": s["start_line"], "end": s["end_line"],
                           "summary": _first_sentence(s.get("doc", "")),
                           "parent": nodes[s["parent"]]["name"] if s.get("parent") in nodes else None}
                          for s in funcs],
        })

    calls_in, calls_out = defaultdict(int), defaultdict(int)
    for e in graph["edges"]:
        if e["type"] == "calls":
            calls_out[e["source"]] += 1
            calls_in[e["target"]] += 1
    start = start_here(entry, files, links, symbols_in, calls_in, calls_out)
    stats = graph["stats"]
    funcs_total = sum(stats["nodes"].get(k, 0) for k in ("function", "method"))
    classes_total = sum(stats["nodes"].get(k, 0) for k in ("class", "interface", "enum", "record"))
    overview = {
        "project": name,
        "stats": {"files": len(file_nodes), "functions": funcs_total, "classes": classes_total,
                  "lines": sum(lines.values()), "calls": stats["edges"].get("calls", 0),
                  "imports": stats["edges"].get("imports", 0)},
        "languages": languages(file_nodes, lines),
        "start_here": start,
        "files": files,
        "map": {"links": [{"source": s, "target": t, **v} for (s, t), v in sorted(links.items())]},
    }
    overview["overview"] = {"text": overview_from_code(overview, nodes), "source": "code"}
    return overview


def languages(file_nodes: list[dict], lines: dict[str, int]) -> list[dict]:
    by = defaultdict(lambda: [0, 0])
    for f in file_nodes:
        lang = LANG_NAMES.get(f.get("language", ""), f.get("language", "other"))
        by[lang][0] += 1
        by[lang][1] += lines[f["id"]]
    total = sum(v[1] for v in by.values()) or 1
    return [{"name": k, "files": v[0], "lines": v[1], "percent": round(100 * v[1] / total)}
            for k, v in sorted(by.items(), key=lambda kv: (-kv[1][1], kv[0]))]


def pick_entry(file_nodes, symbols_in, src, outgoing, incoming) -> str | None:
    """The file where the program most likely starts. Highest score wins;
    ties are broken by path so the choice never changes."""
    best, best_score = None, None
    for f in file_nodes:
        path = f["id"]
        if guess_layer(path) == "Tests" or path.endswith(".h"):
            continue
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        names = {s["name"] for s in symbols_in[path]}
        score = 0.0
        if "main" in names:
            score += 5
        if stem in ENTRY_NAMES:
            score += 4
        if _has_main_guard(os.path.join(src, path)):
            score += 4
        score += min(outgoing[path], 6) * 0.5 - min(incoming[path], 6) * 0.4
        score -= path.count("/") * 0.3            # top-level files are more likely starts
        key = (score, _neg(path))
        if best_score is None or key > best_score:
            best, best_score = path, key
    return best


def start_here(entry: str | None, files: list[dict], links: dict, symbols_in,
               calls_in: dict, calls_out: dict) -> dict:
    """A reading order: the entry file first, then the files it uses (breadth
    first), then everything else by how connected it is."""
    if entry is None:
        return {"file": None, "function": None, "steps": []}
    uses = defaultdict(list)
    for (s, t), v in sorted(links.items()):
        uses[s].append(t)
    order, seen, queue = [], {entry}, [entry]
    reasons = {entry: "The program starts here."}
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in uses[cur]:
            if nxt not in seen:
                seen.add(nxt)
                reasons[nxt] = f"{_base(cur)} uses code from this file."
                queue.append(nxt)
    by_path = {f["path"]: f for f in files}
    rest = sorted((p for p in by_path if p not in seen and not p.endswith(".h")),
                  key=lambda p: (-by_path[p]["connections"], p))
    for p in rest:
        reasons[p] = "Read this after the files above."
    steps = [{"file": p, "reason": reasons[p]} for p in order + rest if not p.endswith(".h")][:8]

    # main() if there is one; else a function nobody calls that calls the
    # most other code (like route() that sends requests to handlers)
    funcs = [s for s in symbols_in[entry] if not s.get("parent")]
    func = next((s for s in funcs if s["name"] == "main"), None)
    if func is None and funcs:
        func = max(funcs, key=lambda s: (calls_in[s["id"]] == 0, calls_out[s["id"]], -s["start_line"]))
    return {"file": entry,
            "function": {"name": func["name"], "line": func["start_line"]} if func else None,
            "steps": steps}


def summary_from_code(path: str, doc: str, funcs: list[dict]) -> str:
    """A one-line description without AI: the file's own top comment, or
    what it defines."""
    first = _first_sentence(doc)
    if first:
        return first
    names = [s["name"] for s in funcs if not s.get("parent")]
    if path.endswith(".h"):
        return "A header file: it lists functions so other files can use them."
    if not names:
        return "No functions here; it may hold settings or a small script."
    shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    word = "function" if len(names) == 1 else "functions"
    return f"Defines {len(names)} {word}: {shown}."


def overview_from_code(ov: dict, nodes: dict) -> str:
    s = ov["stats"]
    langs = [l["name"] for l in ov["languages"]]
    lang = langs[0] if len(langs) == 1 else " and ".join([", ".join(langs[:-1]), langs[-1]])
    files_word = "file" if s["files"] == 1 else "files"
    func_word = "function" if s["functions"] == 1 else "functions"
    parts = [f"{ov['project']} is a {lang} project with {s['files']} {files_word} "
             f"and {s['functions']} {func_word} ({s['lines']} lines of code)."]
    start = ov["start_here"]
    if start["file"]:
        where = f"The program starts in {_base(start['file'])}"
        if start["function"]:
            where += f", in {start['function']['name']}()"
        doc = _first_sentence(nodes.get(start["file"], {}).get("doc", ""))
        parts.append(where + "." + (f" That file says: “{doc}”" if doc else ""))
    busiest = sorted(ov["files"], key=lambda f: (-len(f["functions"]), f["path"]))[:1]
    if busiest and len(ov["files"]) > 1 and busiest[0]["functions"]:
        b = busiest[0]
        parts.append(f"Most of the work happens in {_base(b['path'])} ({len(b['functions'])} functions).")
    if s["calls"]:
        parts.append(f"RepoSage found {s['calls']} places where one function calls another; "
                     "the map below shows how the files connect.")
    return " ".join(parts)


# ---------------------------------------------------------------------------

def _file_links(graph: dict) -> dict[tuple[str, str], dict]:
    links: dict[tuple[str, str], dict] = defaultdict(lambda: {"imports": 0, "calls": 0})
    for e in graph["edges"]:
        s, t = e["source"].split("::", 1)[0], e["target"].split("::", 1)[0]
        if s == t:
            continue
        if e["type"] == "imports":
            links[(s, t)]["imports"] = 1
        elif e["type"] == "calls":
            links[(s, t)]["calls"] += 1
    return dict(links)


def _count_lines(path: str) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _has_main_guard(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(200_000)
    except OSError:
        return False
    return bool(re.search(r"__name__\s*==\s*['\"]__main__['\"]|public\s+static\s+void\s+main\s*\(", text))


def _first_sentence(doc: str) -> str:
    doc = " ".join((doc or "").split())
    if not doc:
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", doc)
    sentence = m.group(1) if m else doc
    return sentence if len(sentence) <= 160 else sentence[:157] + "..."


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _neg(path: str) -> tuple:
    """Sort key that prefers alphabetically EARLIER paths when used with max()."""
    return tuple(-ord(c) for c in path)


def load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
