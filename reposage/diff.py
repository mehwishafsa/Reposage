"""/reposage:diff: what do my current changes affect?

  1. Ask git what changed (uncommitted work, or everything since the point
     where this branch left --base), down to exact line numbers.
  2. Map changed lines to the functions/classes that contain them.
  3. Ripple: walk the call graph BACKWARDS - who calls the changed code
     (depth 1), who calls those (depth 2), ... - remembering guessed links.
  4. Tests: a test "covers" a function if the test reaches it through calls.
  5. Risk: Low / Medium / High from a few simple, stated rules.

The output is a small "impact pack" (the changed lines plus just the
calling line of each affected function) for Claude to explain, and a saved
change view that the dashboard shows like an Answer Path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import defaultdict, deque
from typing import Optional

from .cache import write_json_atomic
from .layers import TESTS, load_overrides, resolve_layer
from .parsers import parser_for

MAX_DEPTH = 3            # how far the ripple goes
MAX_AFFECTED = 60        # cap on affected functions listed
TEST_SEARCH_DEPTH = 6    # how far back we look for tests
MAX_HUNK_LINES = 40      # diff lines shown per changed function
SYMBOL_TYPES = ("class", "interface", "enum", "record", "function", "method")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffError(Exception):
    pass


# ----------------------------------------------------------------------
# 1. git
# ----------------------------------------------------------------------

def _git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise DiffError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def read_changes(repo: str, base: Optional[str] = None) -> dict:
    """Changed files with their hunks, relative to `repo`."""
    if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
        raise DiffError("this folder is not a git repository")
    if base:
        ref = _git(repo, "merge-base", base, "HEAD", check=False).strip()
        if not ref:
            ref = base                      # no common ancestor: compare directly
        label = f"changes since this branch left {base}"
    else:
        ref = "HEAD"
        label = "uncommitted changes"
    if not _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).strip():
        raise DiffError(f"unknown git revision: {base or 'HEAD'} (no commits yet?)")
    text = _git(repo, "diff", "--relative", "--no-color", "--no-ext-diff", "-M",
                "--unified=0", ref)
    files = _parse_diff(text)
    # New files git doesn't track yet count as fully added.
    for path in _git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        if path and not path.startswith(".reposage/") and path not in files:
            try:
                with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
                    n = len(fh.read().splitlines())
            except OSError:
                continue
            files[path] = {"path": path, "old_path": None, "status": "added",
                           "hunks": [{"old": (0, 0), "new": (1, n), "lines": []}],
                           "added": n, "removed": 0}
    return {"ref": ref, "label": label, "base": base, "files": files}


def _parse_diff(text: str) -> dict:
    files: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": None, "old_path": None, "status": "modified", "hunks": [],
                   "added": 0, "removed": 0}
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            if m:
                cur["old_path"], cur["path"] = m.group(1), m.group(2)
                files[cur["path"]] = cur
        elif cur is None:
            continue
        elif line.startswith("--- "):
            if line == "--- /dev/null":
                cur["status"], cur["old_path"] = "added", None
        elif line.startswith("+++ "):
            if line == "+++ /dev/null":
                cur["status"] = "deleted"
        elif line.startswith("rename from ") and cur["status"] == "modified":
            cur["status"] = "renamed"
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                o, ol, n, nl = (int(m.group(1)), int(m.group(2) or 1),
                                int(m.group(3)), int(m.group(4) or 1))
                cur["hunks"].append({"old": (o, ol), "new": (n, nl), "lines": [line]})
        elif cur["hunks"] and line[:1] in "+-\\":
            cur["hunks"][-1]["lines"].append(line)
            if line.startswith("+"):
                cur["added"] += 1
            elif line.startswith("-"):
                cur["removed"] += 1
    return files


# ----------------------------------------------------------------------
# 2-5. impact
# ----------------------------------------------------------------------

def analyze(repo: str, graph: dict, changes: dict, max_depth: int = MAX_DEPTH) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    overrides, _ = load_overrides(repo)
    layer_of = {n["id"]: resolve_layer(n["id"], n.get("layer"), overrides)[0]
                for n in graph["nodes"] if n["type"] == "file"}

    def is_test(node_id: str) -> bool:
        return layer_of.get(node_id.split("::")[0]) == TESTS

    callers: dict[str, list[dict]] = defaultdict(list)
    importers: dict[str, list[dict]] = defaultdict(list)
    for e in graph["edges"]:
        if e["type"] == "calls":
            callers[e["target"]].append(e)
        elif e["type"] == "imports":
            importers[e["target"]].append(e)
    defs_by_file: dict[str, list[dict]] = defaultdict(list)
    for n in graph["nodes"]:
        if n["type"] in SYMBOL_TYPES:
            defs_by_file[n["path"]].append(n)

    changed: dict[str, dict] = {}        # node id -> info
    file_level: list[str] = []           # files changed outside any function
    removed: list[dict] = []
    other_files: list[str] = []
    for path, f in sorted(changes["files"].items()):
        code_path = path if f["status"] != "deleted" else None
        if parser_for(path) is None:
            other_files.append(path)
            continue
        new_lines, touch_points = _changed_lines(f)
        defs = defs_by_file.get(code_path, []) if code_path else []
        hit = [d for d in defs if any(d["start_line"] <= ln <= d["end_line"] for ln in new_lines | touch_points)]
        # keep the innermost definitions (a changed method, not its whole class)
        innermost = [d for d in hit if not any(o["parent"] == d["id"] or _is_inside(o, d)
                                               for o in hit if o["id"] != d["id"])]
        old_defs = _old_definitions(repo, changes["ref"], f)
        for d in innermost:
            qual = d["id"].split("::", 1)[1]
            status = "added" if (f["status"] == "added" or qual not in old_defs) else "modified"
            changed[d["id"]] = {
                "id": d["id"], "status": status,
                "signature_changed": status == "modified" and d["start_line"] in new_lines,
                "hunk": _hunk_for(f, d["start_line"], d["end_line"]),
            }
        in_defs = {ln for d in defs for ln in range(d["start_line"], d["end_line"] + 1)}
        outside = (new_lines - in_defs) - _comment_or_blank_lines(repo, code_path) if code_path else set()
        if f["status"] != "added" and (outside or (touch_points - in_defs and _removed_code(f))):
            file_level.append(code_path)
        # functions that existed before but are gone now
        now = {d["id"].split("::", 1)[1] for d in defs}
        for qual, line in sorted(old_defs.items()):
            if qual not in now:
                removed.append({"path": f["old_path"] or path, "qual": qual, "line": line,
                                "name": qual.split(".")[-1]})

    # Ripple: callers of callers, breadth-first, best (confident) path wins.
    affected: dict[str, dict] = {}
    queue = deque((cid, 0, False) for cid in sorted(changed))
    seen = {cid: (0, False) for cid in changed}
    while queue:
        node_id, depth, guessed = queue.popleft()
        if depth >= max_depth:
            continue
        for e in sorted(callers.get(node_id, []), key=lambda e: (e.get("confidence") != "high", e["source"])):
            src = e["source"]
            if src not in nodes or nodes[src]["type"] not in SYMBOL_TYPES or is_test(src):
                continue
            g = guessed or e.get("confidence") != "high"
            prev = seen.get(src)
            if prev is not None and (prev[0] < depth + 1 or (prev[0] == depth + 1 and not prev[1])):
                continue
            seen[src] = (depth + 1, g)
            if src not in changed:
                affected[src] = {"id": src, "depth": depth + 1, "guessed": g, "calls": node_id,
                                 "line": e.get("line"), "confidence": e.get("confidence", "high")}
            queue.append((src, depth + 1, g))
    # Files changed outside functions (imports, top-level code): their importers.
    affected_files = []
    for path in file_level:
        for e in importers.get(path, []):
            if not is_test(e["source"]):
                affected_files.append({"id": e["source"], "imports": path, "line": e.get("line")})

    ordered = sorted(affected.values(), key=lambda a: (a["depth"], a["guessed"], a["id"]))
    hidden = max(0, len(ordered) - MAX_AFFECTED)
    ordered = ordered[:MAX_AFFECTED]

    # Tests: who reaches each changed / affected function through calls?
    tests_for = {nid: _tests_reaching(nid, callers, is_test) for nid in list(changed) + [a["id"] for a in ordered]}
    for path in file_level:
        tests_for[path] = sorted({e["source"].split("::")[0] for e in importers.get(path, []) if is_test(e["source"])})

    # Removed functions that current code still calls by name.
    still_called = _still_called(repo, removed)

    risk, reasons = _risk(changed, ordered, tests_for, still_called, file_level, affected_files, hidden)
    return {"label": changes["label"], "base": changes["base"], "ref": changes["ref"],
            "files": changes["files"], "other_files": other_files, "changed": changed,
            "file_level": file_level, "affected": ordered, "affected_hidden": hidden,
            "affected_files": affected_files, "removed": removed, "still_called": still_called,
            "tests_for": tests_for, "risk": risk, "reasons": reasons, "nodes": nodes,
            "layer_of": layer_of}


def _changed_lines(f: dict) -> tuple[set, set]:
    """Lines added/changed in the new file, and 'touch points' where lines
    were only deleted (the deletion sits between line n and n+1)."""
    new_lines, touch = set(), set()
    for h in f["hunks"]:
        start, length = h["new"]
        if length > 0:
            new_lines.update(range(start, start + length))
        else:
            touch.update({max(1, start), start + 1})
    return new_lines, touch


def _comment_or_blank_lines(repo: str, path: str) -> set:
    """Line numbers that are blank or only a comment (not a real code change)."""
    try:
        with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return set()
    return {i for i, ln in enumerate(lines, start=1)
            if not ln.strip() or ln.strip().startswith(("#", "//", "/*", "*", "--"))}


def _removed_code(f: dict) -> bool:
    """Did the diff delete any line that was real code (not blank/comment)?"""
    return any(ln.startswith("-") and ln[1:].strip()
               and not ln[1:].strip().startswith(("#", "//", "/*", "*"))
               for h in f["hunks"] for ln in h["lines"][1:])


def _is_inside(inner: dict, outer: dict) -> bool:
    return (inner["path"] == outer["path"] and inner["id"].startswith(outer["id"].split("#")[0] + ".")
            and outer["start_line"] <= inner["start_line"] and inner["end_line"] <= outer["end_line"])


def _old_definitions(repo: str, ref: str, f: dict) -> dict[str, int]:
    """Qualified names -> line, as the file was at `ref` (before the change)."""
    if f["status"] == "added" or not f["old_path"]:
        return {}
    parser = parser_for(f["old_path"])
    if parser is None:
        return {}
    try:
        old = subprocess.run(["git", "show", f"{ref}:./{f['old_path']}"], cwd=repo,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    facts = parser.parse(f["old_path"], old)
    return {d.id.split("::", 1)[1]: d.start_line for d in facts.definitions}


def _hunk_for(f: dict, start: int, end: int) -> list[str]:
    """Diff lines of the hunks that touch lines start..end."""
    out = []
    for h in f["hunks"]:
        ns, nl = h["new"]
        lo, hi = ns, ns + max(nl, 1) - 1
        if hi >= start and lo <= end + 1:
            out += h["lines"]
    if len(out) > MAX_HUNK_LINES:
        out = out[:MAX_HUNK_LINES] + [f"… {len(out) - MAX_HUNK_LINES} more diff lines"]
    return out


def _tests_reaching(node_id: str, callers: dict, is_test) -> list[str]:
    """Test functions that reach node_id through calls (up to a few hops)."""
    found, seen = set(), {node_id}
    frontier = [node_id]
    for _ in range(TEST_SEARCH_DEPTH):
        nxt = []
        for nid in frontier:
            for e in callers.get(nid, []):
                src = e["source"]
                if src in seen:
                    continue
                seen.add(src)
                if is_test(src):
                    found.add(src)
                else:
                    nxt.append(src)
        frontier = nxt
    return sorted(found)[:6]


def _still_called(repo: str, removed: list[dict]) -> list[dict]:
    """Current code that still calls a removed function by its name."""
    if not removed:
        return []
    try:
        with open(os.path.join(repo, ".reposage", "cache", "facts.json"), encoding="utf-8") as fh:
            cache = json.load(fh)["files"]
    except (OSError, ValueError, KeyError):
        return []
    names = {r["name"]: r for r in removed}
    hits = []
    for path, entry in sorted(cache.items()):
        for c in entry["facts"]["calls"]:
            if c["name"] in names:
                hits.append({"removed": names[c["name"]]["qual"], "caller": c["caller"],
                             "path": path, "line": c["line"]})
    return hits[:20]


def _risk(changed, affected, tests_for, still_called, file_level, affected_files, hidden):
    reasons: list[tuple[str, str]] = []           # (level, reason)
    direct = [a for a in affected if a["depth"] == 1]
    untested_changed = [c for c in changed if not tests_for.get(c)]
    untested_direct = [a for a in direct if not tests_for.get(a["id"])]
    short = lambda i: i.split("::")[-1]

    if still_called:
        s = still_called[0]
        reasons.append(("High", f"{s['removed']} was removed, but {short(s['caller'])} "
                                f"({s['path']}:{s['line']}) still calls it"))
    for cid, c in changed.items():
        if c["signature_changed"] and any(a["calls"] == cid for a in direct):
            n = sum(1 for a in direct if a["calls"] == cid)
            level = "High" if untested_direct or not tests_for.get(cid) else "Medium"
            reasons.append((level, f"the signature (first line) of {short(cid)} changed and "
                                   f"{n} function(s) call it"))
    total = len(affected) + hidden
    if total >= 15:
        reasons.append(("High", f"the change ripples out to {total} functions"))
    if untested_changed and direct:
        reasons.append(("Medium", f"{len(untested_changed)} changed function(s) have no tests "
                                  f"but are used by {len(direct)} other function(s)"))
    elif untested_direct:
        reasons.append(("Medium", f"{len(untested_direct)} directly affected function(s) have no tests"))
    if file_level and affected_files:
        reasons.append(("Medium", f"top-level code or imports changed in {len(file_level)} file(s) "
                                  f"that {len(affected_files)} other file(s) import"))
    if any(a["guessed"] for a in affected):
        reasons.append(("Note", "some links in the ripple are guessed (matched by name only)"))
    if not [r for r in reasons if r[0] in ("High", "Medium")]:
        if not changed and not file_level:
            reasons.append(("Low", "no code functions changed"))
        elif not direct:
            reasons.append(("Low", "nothing else in the repo calls the changed code"))
        else:
            reasons.append(("Low", "the changed code and its direct callers are covered by tests"))
    order = {"High": 3, "Medium": 2, "Low": 1, "Note": 0}
    level = max((r[0] for r in reasons), key=lambda x: order[x])
    return level, [r[1] for r in sorted(reasons, key=lambda r: -order[r[0]])]


# ----------------------------------------------------------------------
# Output: the impact pack + the saved change view
# ----------------------------------------------------------------------

def render_pack(repo: str, a: dict) -> str:
    nodes, sources = a["nodes"], {}

    def src_line(path: str, line: Optional[int]) -> str:
        if not line:
            return ""
        if path not in sources:
            try:
                with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
                    sources[path] = fh.read().splitlines()
            except OSError:
                sources[path] = []
        lines = sources[path]
        return f"{line:>5} | {lines[line - 1].strip()}" if 0 < line <= len(lines) else ""

    def tests_text(nid: str) -> str:
        t = a["tests_for"].get(nid) or []
        return ("tests: " + ", ".join(x.split("/")[-1] for x in t)) if t else "tests: NONE found"

    files = a["files"]
    add = sum(f["added"] for f in files.values())
    rem = sum(f["removed"] for f in files.values())
    direct = [x for x in a["affected"] if x["depth"] == 1]
    out = [f"# RepoSage change impact ({a['label']})", "",
           f"{len(files)} file(s) changed (+{add} -{rem}); {len(a['changed'])} function(s) changed; "
           f"{len(direct)} directly and {len(a['affected']) - len(direct) + a['affected_hidden']} "
           f"indirectly affected.",
           f"Computed risk: **{a['risk'].upper()}**",
           *[f"- {r}" for r in a["reasons"]], "",
           "Explain ONLY from this pack. Cite code as `file:line`.", ""]
    if not a["changed"] and not a["file_level"] and not a["removed"]:
        out.append("No code changes found (only non-code files, or nothing at all).")
    out.append("## Changed code")
    for k, (cid, c) in enumerate(sorted(a["changed"].items()), start=1):
        n = nodes[cid]
        flags = [c["status"]] + (["SIGNATURE CHANGED"] if c["signature_changed"] else [])
        out += [f"### [C{k}] {cid.split('::', 1)[1]}  ({n['type']}, {a['layer_of'].get(n['path'], '?')} layer; "
                f"{', '.join(flags)})",
                f"- where: {n['path']}:{n['start_line']}-{n['end_line']}",
                f"- {tests_text(cid)}"]
        if n.get("summary"):
            out.append(f"- summary (before the change): {n['summary']}")
        out += _function_with_changes(repo, n, a["files"].get(n["path"]), c["hunk"])
        out.append("")
    for path in a["file_level"]:
        out.append(f"- top-level code/imports changed in {path} ({tests_text(path)})")
    if a["removed"]:
        out.append("## Removed functions")
        out += [f"- {r['qual']} (was {r['path']}:{r['line']})" for r in a["removed"]]
        out += [f"  - STILL CALLED by {s['caller'].split('::')[-1]} at {s['path']}:{s['line']}"
                for s in a["still_called"]]
        out.append("")
    if a["affected"]:
        out.append("## Affected code (who calls the changed code)")
        for depth in range(1, MAX_DEPTH + 1):
            group = [x for x in a["affected"] if x["depth"] == depth]
            if not group:
                continue
            out.append(f"### depth {depth} ({'direct' if depth == 1 else 'indirect'})")
            for x in group:
                n = nodes[x["id"]]
                guess = " (GUESSED link: matched by name only)" if x["confidence"] != "high" else ""
                out.append(f"- {x['id'].split('::', 1)[1]}  {n['path']}:{n['start_line']}  "
                           f"calls {x['calls'].split('::')[-1]} at line {x['line']}{guess}; {tests_text(x['id'])}")
                if n.get("summary"):
                    out.append(f"  summary: {n['summary']}")
                line = src_line(n["path"], x["line"])
                if line:
                    out.append(f"  `{line}`")
        if a["affected_hidden"]:
            out.append(f"- … and {a['affected_hidden']} more (not listed)")
        out.append("")
    if a["affected_files"]:
        out.append("## Files importing a changed file")
        out += [f"- {x['id']} imports {x['imports']}" for x in a["affected_files"]]
        out.append("")
    all_tests = sorted({t for ts in a["tests_for"].values() for t in ts})
    out.append("## Tests that exercise the change")
    out += [f"- {t}" for t in all_tests] or ["- none found (no test reaches the changed code through calls)"]
    if a["other_files"]:
        out += ["", f"Other changed files (not code): {', '.join(a['other_files'][:15])}"]
    return "\n".join(out)


FULL_FUNCTION_LINES = 50


def _function_with_changes(repo: str, n: dict, f: Optional[dict], hunk: list[str]) -> list[str]:
    """The whole new version of a changed function (if short), changed lines
    marked with "+", followed by the lines that were removed. Gives Claude
    the surrounding code without sending whole files."""
    start, end = n["start_line"], n["end_line"]
    if end - start + 1 > FULL_FUNCTION_LINES or f is None:
        return ["~~~diff", *hunk, "~~~"] if hunk else []
    try:
        with open(os.path.join(repo, n["path"]), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ["~~~diff", *hunk, "~~~"] if hunk else []
    changed, _ = _changed_lines(f)
    out = ["New version (+ = added or changed line):", "~~~"]
    out += [f"{'+' if i in changed else ' '}{i:>5} | {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1)]
    out.append("~~~")
    removed = [ln for ln in hunk if ln.startswith("-")]
    if removed:
        out += ["Removed lines:", "~~~", *removed, "~~~"]
    return out


def save_view(repo: str, a: dict, summary: str = "") -> dict:
    """Save the change view for the dashboard (next to the Answer Paths)."""
    nodes = []
    for cid, c in sorted(a["changed"].items()):
        nodes.append({"id": cid, "role": "changed", "depth": 0, "status": c["status"],
                      "tested": bool(a["tests_for"].get(cid))})
    for x in a["affected"]:
        nodes.append({"id": x["id"], "role": "direct" if x["depth"] == 1 else "indirect",
                      "depth": x["depth"], "guessed": x["guessed"],
                      "tested": bool(a["tests_for"].get(x["id"]))})
    links = [{"source": x["id"], "target": x["calls"], "confidence": x["confidence"]} for x in a["affected"]]
    title = ("What my uncommitted changes affect" if not a["base"]
             else f"What my changes since {a['base']} affect")
    record = {
        "id": f"{time.strftime('%Y%m%d-%H%M%S')}-diff", "kind": "diff", "question": title,
        "answer": " ".join(summary.split())[:600], "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "risk": a["risk"], "reasons": a["reasons"], "nodes": nodes, "links": links,
        "files_changed": sorted(a["files"]), "removed": [r["qual"] for r in a["removed"]],
        "tests": sorted({t for ts in a["tests_for"].values() for t in ts}),
    }
    write_json_atomic(os.path.join(repo, ".reposage", "answers", f"{record['id']}.json"), record)
    return record


def add_summary(repo: str, summary: str) -> Optional[dict]:
    """Attach Claude's plain-English explanation to the latest change view."""
    folder = os.path.join(repo, ".reposage", "answers")
    names = sorted((n for n in os.listdir(folder) if n.endswith("-diff.json")), reverse=True) \
        if os.path.isdir(folder) else []
    if not names:
        return None
    path = os.path.join(folder, names[0])
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["answer"] = " ".join(summary.split())[:600]
    write_json_atomic(path, record)
    return record
