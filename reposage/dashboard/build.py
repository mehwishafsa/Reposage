"""Build .reposage/dashboard.html from .reposage/graph.json.

The dashboard is ONE self-contained HTML file: page + JavaScript + graph
data all inline. No server, no internet, works from file://, and it can be
attached to an e-mail or opened on a judge's laptop as-is.

graph.json is written for humans and tools (one readable object per node).
For the browser we repack it into a compact, column-based format:

    graph.json:  {"id": "src/a.py::f", "type": "function", "path": "src/a.py", ...}
    dashboard:   sym.name[i] = "f", sym.file[i] = 3, sym.kind[i] = 4, ...

Column arrays of small integers are several times smaller than repeated
keys and full ID strings, and much faster for the browser to parse
(gson: 6.4 MB graph.json -> ~0.9 MB of dashboard data).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Optional

from .. import __version__
from ..layers import ALL_GROUPS, load_overrides, resolve_layer

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = ("d3-dispatch", "d3-quadtree", "d3-timer", "d3-force")
SYMBOL_KINDS = ("class", "interface", "enum", "record", "function", "method")

DOC_LIMIT = 400        # characters of docstring kept per node
SIGNATURE_LIMIT = 200


def build_data(graph: dict, repo_root: str) -> dict:
    """Repack graph.json into the dashboard's compact format."""
    file_nodes = sorted((n for n in graph["nodes"] if n["type"] == "file"),
                        key=lambda n: n["id"])
    sym_nodes = sorted((n for n in graph["nodes"] if n["type"] in SYMBOL_KINDS),
                       key=lambda n: n["id"])
    file_idx = {n["id"]: i for i, n in enumerate(file_nodes)}
    sym_idx = {n["id"]: i for i, n in enumerate(sym_nodes)}
    languages = sorted({n.get("language", "") for n in file_nodes})
    group_idx = {g: i for i, g in enumerate(ALL_GROUPS)}

    # ---- files -------------------------------------------------------
    overrides, _ = load_overrides(repo_root)
    files = {"path": [], "lang": [], "group": [], "src": [], "doc": [],
             "summary": [], "stale": [], "tags": [], "ext": []}
    for n in file_nodes:
        layer, source = resolve_layer(n["id"], n.get("layer"), overrides)
        files["path"].append(n["id"])
        files["lang"].append(languages.index(n.get("language", "")))
        files["group"].append(group_idx[layer])
        files["src"].append({"guess": 0, "ai": 1, "user": 2}[source])
        files["doc"].append(_clip(n.get("doc", ""), DOC_LIMIT))
        files["summary"].append(n.get("summary") or "")
        files["stale"].append(1 if n.get("ai_stale") else 0)
        files["tags"].append(n.get("tags") or [])
        files["ext"].append(n.get("external_imports", []))

    # ---- symbols (classes, functions, methods) -----------------------
    sym = {"name": [], "qual": [], "kind": [], "file": [], "parent": [],
           "l0": [], "l1": [], "sig": [], "doc": [], "summary": []}
    for n in sym_nodes:
        sym["name"].append(n["name"])
        sym["qual"].append(n["id"].split("::", 1)[1])
        sym["kind"].append(SYMBOL_KINDS.index(n["type"]))
        sym["file"].append(file_idx[n["path"]])
        sym["parent"].append(sym_idx.get(n.get("parent") or "", -1))
        sym["l0"].append(n["start_line"])
        sym["l1"].append(n["end_line"])
        sym["sig"].append(_clip(n.get("signature", ""), SIGNATURE_LIMIT))
        sym["doc"].append(_clip(n.get("doc", ""), DOC_LIMIT))
        sym["summary"].append(n.get("summary") or "")

    # ---- edges -------------------------------------------------------
    # calls between symbols: flat [source, target, confidence(1=high,0=medium), ...]
    calls: list[int] = []
    # file -> file dependencies, merged from imports and calls
    file_links: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])

    def file_of(node_id: str) -> Optional[int]:
        return file_idx.get(node_id.split("::", 1)[0])

    for e in graph["edges"]:
        if e["type"] == "calls":
            s, t = sym_idx.get(e["source"]), sym_idx.get(e["target"])
            if s is not None and t is not None:
                calls += [s, t, 1 if e.get("confidence") == "high" else 0]
            fs, ft = file_of(e["source"]), file_of(e["target"])
            if fs is not None and ft is not None and fs != ft:
                file_links[(fs, ft)][1] += 1
        elif e["type"] == "imports":
            fs, ft = file_idx.get(e["source"]), file_idx.get(e["target"])
            if fs is not None and ft is not None:
                file_links[(fs, ft)][0] = 1
    # flat [source, target, imports(0/1), number of calls, ...]
    flinks: list[int] = []
    for (s, t), (imp, n_calls) in sorted(file_links.items()):
        flinks += [s, t, imp, n_calls]

    return {
        "format": 1,
        "generator": f"reposage {__version__}",
        "project": os.path.basename(os.path.abspath(repo_root)),
        "root": os.path.abspath(repo_root).replace(os.sep, "/"),
        "groups": list(ALL_GROUPS),
        "languages": languages,
        "kinds": list(SYMBOL_KINDS),
        "files": files,
        "sym": sym,
        "calls": calls,
        "flinks": flinks,
        "tours": graph.get("tours", []),
        "answers": _answers(repo_root, sym_idx, file_idx),
    }


def render_html(data: dict) -> str:
    """Fill the page template with the libraries and the data."""
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        page = f.read()
    vendor = "\n".join(_read(os.path.join(HERE, "vendor", f"{name}.min.js"))
                       for name in VENDOR)
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    # "</script>" inside the data would end the <script> tag early.
    payload = payload.replace("</", "<\\/")
    return (page.replace("/*__REPOSAGE_VENDOR__*/", vendor)
                .replace("__REPOSAGE_DATA__", payload))


def write_dashboard(graph: dict, repo_root: str, out_path: str) -> dict:
    """Build and save the dashboard; returns the compact data for reporting."""
    data = build_data(graph, repo_root)
    html = render_html(data)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    os.replace(tmp, out_path)
    return data


# ----------------------------------------------------------------------

def _answers(repo_root: str, sym_idx: dict, file_idx: dict) -> list[dict]:
    """Saved Answer Paths (/reposage:chat) and change views (/reposage:diff),
    with node ids turned into indexes."""
    from ..chat import load_answers
    out = []
    for rec in load_answers(repo_root):
        if rec.get("kind") == "diff":
            view = _diff_view(rec, sym_idx, file_idx)
            if view:
                out.append(view)
            continue
        steps = []
        for st in rec.get("steps", []):
            if st["id"] in sym_idx:
                steps.append({"type": "sym", "i": sym_idx[st["id"]], "label": st.get("label", "")})
            elif st["id"] in file_idx:
                steps.append({"type": "file", "i": file_idx[st["id"]], "label": st.get("label", "")})
        if steps:
            out.append({k: rec.get(k) for k in ("id", "question", "answer", "created",
                                                 "confidence", "confidence_note", "hops")}
                       | {"steps": steps})
    return out


def _diff_view(rec: dict, sym_idx: dict, file_idx: dict) -> Optional[dict]:
    nodes = [{"i": sym_idx[n["id"]], "role": n["role"], "depth": n["depth"],
              "tested": n.get("tested", False), "guessed": n.get("guessed", False),
              "status": n.get("status", "")}
             for n in rec.get("nodes", []) if n["id"] in sym_idx]
    links = [{"s": sym_idx[l["source"]], "t": sym_idx[l["target"]], "high": l.get("confidence") == "high"}
             for l in rec.get("links", []) if l["source"] in sym_idx and l["target"] in sym_idx]
    changed_files = [file_idx[p] for p in rec.get("files_changed", []) if p in file_idx]
    if not nodes and not changed_files:
        return None
    return {"kind": "diff", "id": rec["id"], "question": rec.get("question", ""),
            "answer": rec.get("answer", ""), "created": rec.get("created", ""),
            "risk": rec.get("risk", ""), "reasons": rec.get("reasons", []),
            "nodes": nodes, "links": links, "changed_files": changed_files,
            # a test is a function, or a whole test file (top-level test(...) calls)
            "tests": [{"type": "sym", "i": sym_idx[t]} if t in sym_idx else {"type": "file", "i": file_idx[t]}
                      for t in rec.get("tests", []) if t in sym_idx or t in file_idx],
            "removed": rec.get("removed", [])}


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()
