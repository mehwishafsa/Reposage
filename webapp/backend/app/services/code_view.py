"""The code explainer: a file (or one function) cut into blocks, each with a
plain-English explanation, plus a flowchart for functions.

    file view      imports, constants, structs, each function, ... (top level)
    function view  the flowchart boxes of that function (see flowchart.py)

Everything here works without AI. explain_ai.py can replace the
explanations with AI-written ones (normal or "explain simpler").
"""

from __future__ import annotations

import os
import re
from typing import Optional

from tree_sitter import Node, Parser

from .. import config  # noqa: F401  (puts the reposage engine on sys.path)
from reposage.parsers import parser_for

from . import analyze, explain
from .flowchart import FUNC_TYPES, build_flowchart

IMPORT_TYPES = {"import_statement", "import_from_statement", "future_import_statement", "preproc_include",
                "import_declaration", "package_declaration"}
CLASS_TYPES = {"class_definition", "class_declaration", "interface_declaration", "enum_declaration",
               "record_declaration", "abstract_class_declaration"}
SKIP_TYPES = {"comment", "line_comment", "block_comment"}
GROUPED = {"imports", "setup", "prototypes"}
LIBRARIES = {
    "stdio.h": "printing and reading input (printf, scanf)", "stdlib.h": "general helpers (malloc, rand, exit)",
    "string.h": "working with text (strlen, strcpy)", "math.h": "maths (sqrt, pow)", "ctype.h": "checking characters",
    "stdbool.h": "true and false", "time.h": "dates and time",
    "os": "files and folders", "sys": "the running program", "json": "reading and writing JSON", "time": "time",
    "random": "random numbers", "math": "maths", "re": "searching text", "datetime": "dates and time",
    "java.util.Scanner": "reading what the user types", "java.util.List": "lists", "java.util.ArrayList": "lists",
}


class ViewError(Exception):
    pass


def load(project_dir: str) -> tuple[dict, dict, dict]:
    graph = analyze.load_json(os.path.join(project_dir, "graph.json"))
    overview = analyze.load_json(os.path.join(project_dir, "overview.json"))
    if graph is None or overview is None:
        raise ViewError("This project's analysis is missing. Please upload it again.")
    nodes = {n["id"]: n for n in graph["nodes"]}
    return graph, overview, nodes


def known_summaries(overview: dict, notes: dict) -> dict[str, str]:
    """function name -> one-line summary, used to explain calls ("Calls add (adds two numbers)")."""
    out: dict[str, str] = {}
    for f in overview["files"]:
        for fn in f["functions"]:
            text = notes.get("functions", {}).get(f"{f['path']}::{fn['name']}") or fn.get("summary")
            if text and fn["name"] not in out:
                out[fn["name"]] = text
    return out


def read_source(project_dir: str, rel: str) -> bytes:
    base = os.path.realpath(os.path.join(project_dir, "src"))
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise ViewError("No such file in this project.")
    with open(full, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------

def function_view(project_dir: str, func_id: str, known: dict[str, str]) -> dict:
    graph, overview, nodes = load(project_dir)
    node = nodes.get(func_id)
    if node is None or node["type"] not in ("function", "method"):
        raise ViewError("No such function in this project.")
    source = read_source(project_dir, node["path"])
    fc = build_flowchart(node["path"], source, node["start_line"], node["end_line"], node["name"], known)
    if fc is None:
        raise ViewError("RepoSage couldn't draw this function.")
    blocks = sorted(fc["boxes"], key=lambda b: (b["kind"] == "end", b["lines"][0], int(b["id"][1:])))
    return {"kind": "function", "id": func_id, "name": node["name"], "path": node["path"],
            "language": node.get("language") or _lang(graph, node["path"]),
            "start": node["start_line"], "end": node["end_line"], "signature": node.get("signature", ""),
            "mermaid": fc["mermaid"], "folded": fc["folded"], "blocks": blocks}


def file_view(project_dir: str, path: str, known: dict[str, str]) -> dict:
    graph, overview, nodes = load(project_dir)
    if path not in graph["files"]:
        raise ViewError("No such file in this project.")
    source = read_source(project_dir, path)
    parser = parser_for(path)
    root = Parser(parser.ts_language(path)).parse(source).root_node
    funcs = sorted((n for n in graph["nodes"] if n.get("path") == path and n["type"] in ("function", "method")),
                   key=lambda n: (n["start_line"], n["id"]))
    by_line = {n["start_line"]: n for n in funcs}
    blocks = FileBlocks(source, parser.name, known, by_line).run(root)
    return {"kind": "file", "path": path, "language": parser.name,
            "text": source.decode("utf-8", "replace"),
            "functions": [{"id": n["id"], "name": n["name"],
                           "label": n["id"].split("::", 1)[1].split("#")[0],
                           "line": n["start_line"], "end": n["end_line"]} for n in funcs],
            "blocks": blocks}


def _lang(graph: dict, path: str) -> str:
    return graph["files"].get(path, {}).get("language", "")


# ---------------------------------------------------------------------------

class FileBlocks:
    """Cut a file into top-level blocks. Comments join the block below them."""

    def __init__(self, source: bytes, lang: str, known: dict[str, str], funcs_by_line: dict[int, dict]):
        self.src, self.lang, self.known, self.funcs = source, lang, known, funcs_by_line
        self.blocks: list[dict] = []

    def text(self, n: Node) -> str:
        return n.text.decode("utf-8", "replace")

    def run(self, root: Node) -> list[dict]:
        items = self.flatten(root)
        items = self.about(items)
        pending: Optional[int] = None          # first line of comments waiting for the next block
        group: list[Node] = []
        group_kind, group_comment = "", None
        for n in items:
            if n.type in SKIP_TYPES:
                if group:
                    self.flush(group, group_kind, group_comment)
                    group = []
                pending = pending or n.start_point[0] + 1
                continue
            kind = self.kind_of(n)
            if group and kind == group_kind and kind in GROUPED:
                group.append(n)
                continue
            if group:
                self.flush(group, group_kind, group_comment)
                group = []
            if kind in GROUPED:
                group, group_kind, group_comment, pending = [n], kind, pending, None
                continue
            self.add_single(n, kind, pending)
            pending = None
        if group:
            self.flush(group, group_kind, group_comment)
        for i, b in enumerate(self.blocks):
            b["id"] = f"b{i}"
        return self.blocks

    def about(self, items: list[Node]) -> list[Node]:
        """A comment or docstring at the very top describes the whole file."""
        if not items:
            return items
        first = items[0]
        is_doc = first.type == "expression_statement" and first.named_children \
            and first.named_children[0].type == "string"
        if (first.type in SKIP_TYPES or is_doc) and first.start_point[0] <= 1:
            raw = self.text(first)
            text = explain._clean(re.sub(r'^(/\*+|//+|"""|\'\'\'|#)|(\*+/|"""|\'\'\')$', "", raw.strip()))
            text = re.sub(r"\s*\*\s+", " ", text).strip()
            shown = explain._cut(text, 160)
            self.blocks.append(self._block("about", "about this file", [first.start_point[0] + 1, _last(first)],
                                           (f"A comment at the top says what this file is for: \u201c{shown}\u201d",
                                            f"The note at the top: \u201c{shown}\u201d")))
            return items[1:]
        return items

    def flatten(self, root: Node) -> list[Node]:
        """Top-level nodes; header guards and single big classes are opened up."""
        out = []
        for n in root.named_children:
            if n.type in ("preproc_ifdef", "preproc_if"):
                guard = n.child_by_field_name("name")
                out.append(guard or n)                              # the guard line itself
                for c in n.named_children:
                    if c.type == "identifier":
                        continue
                    name = c.child_by_field_name("name") if c.type == "preproc_def" else None
                    if guard is not None and name is not None and name.text == guard.text:
                        continue                                    # `#define CALC_H` belongs to the guard
                    out.append(c)
            elif n.type in CLASS_TYPES or n.type == "decorated_definition" and _inner(n).type in CLASS_TYPES:
                out.append(n)
            elif n.type == "export_statement" and n.named_children:
                out.append(n.named_children[-1] if n.named_children[-1].type in FUNC_TYPES | CLASS_TYPES
                           | {"lexical_declaration"} else n)
            else:
                out.append(n)
        return out

    def kind_of(self, n: Node) -> str:
        t = n.type
        inner = _inner(n)
        if t in IMPORT_TYPES:
            return "imports"
        if t == "identifier":
            return "guard"
        if inner.type in FUNC_TYPES or self._function_at(n):
            return "function"
        if inner.type in CLASS_TYPES:
            return "class"
        if t in ("struct_specifier", "type_definition", "enum_specifier") or \
                (t == "declaration" and n.child_by_field_name("type") is not None
                 and n.child_by_field_name("type").type == "struct_specifier"
                 and n.child_by_field_name("declarator") is None):
            return "type"
        if t == "declaration" and _has_function_declarator(n):
            return "prototypes"
        if t == "if_statement" and "__name__" in self.text(n.child_by_field_name("condition") or n):
            return "main_guard"
        return "setup"

    def _function_at(self, n: Node) -> Optional[dict]:
        for line in range(n.start_point[0] + 1, n.start_point[0] + 4):
            f = self.funcs.get(line)
            if f and f["end_line"] == n.end_point[0] + 1 - (1 if n.end_point[1] == 0 else 0):
                return f
            if f and f["end_line"] == n.end_point[0] + 1:
                return f
        return None

    def add_single(self, n: Node, kind: str, comment_line: Optional[int]) -> None:
        first = comment_line or n.start_point[0] + 1
        lines = [first, _last(n)]
        if kind == "guard":
            pair = ("A header guard: these `#ifndef`/`#define` lines make sure this file is only read once, "
                    "even if several files #include it.", "Makes sure this file is only read once.")
            self.blocks.append(self._block("guard", "header guard", [n.start_point[0] + 1, n.start_point[0] + 2],
                                           pair))
            return
        if kind == "function":
            f = self._function_at(n) or {}
            name = f.get("name") or _name_of(n, self)
            summary = self.known.get(name) or analyze._first_sentence(f.get("doc", "")) if f else ""
            params = explain.param_names(self.text(_params(n) or n)[:300], self.lang) if _params(n) else []
            sig = f"{name}({', '.join(params)})"
            pair = ((f"Defines the function `{sig}`" + (f": {summary.rstrip('.')}." if summary else ".")
                     + " Pick it above to see its steps and flowchart."),
                    f"A recipe called `{name}`" + (f": {summary.rstrip('.').lower()}." if summary else "."))
            self.blocks.append(self._block("function", sig, lines, pair, function_id=f.get("id")))
            return
        if kind == "class":
            name = _name_of(n, self)
            pair = (f"Defines `{name}`, a class: a blueprint that groups data and the functions that work on it. "
                    "Its functions are listed above.",
                    f"A blueprint called `{name}`.")
            self.blocks.append(self._block("class", f"class {name}", lines, pair))
            return
        if kind == "type":
            name = _name_of(n, self)
            fields = re.findall(r"[\w\s\*]+?\s\**(\w+)\s*(?:\[\w*\])?\s*;", self.text(n))
            shown = explain._and([f"`{f}`" for f in fields[:6]])
            pair = (f"Describes a new kind of value, `{name}`" + (f", made of {shown}" if shown else "") + ".",
                    f"A new kind of box, `{name}`" + (f", with {shown} inside" if shown else "") + ".")
            self.blocks.append(self._block("type", f"struct {name}", lines, pair))
            return
        if kind == "main_guard":
            pair = ("This part only runs when you start this file directly (not when another file imports it). "
                    "It starts the program.", "Start the program here.")
            self.blocks.append(self._block("main", "start the program", lines, pair))
            return
        self.flush([n], "setup", comment_line)

    def flush(self, group: list[Node], kind: str, comment_line: Optional[int]) -> None:
        if not group:
            return
        lines = [comment_line or group[0].start_point[0] + 1, _last(group[-1])]
        texts = [self.text(n) for n in group]
        if kind == "imports":
            pair = self._imports(texts)
            self.blocks.append(self._block("imports", "imports", lines, pair))
        elif kind == "prototypes":
            names = [n for t in texts for n in re.findall(r"(\w+)\s*\(", t)]
            shown = explain._and([f"`{n}`" for n in names[:8]]) + (" and more" if len(names) > 8 else "")
            pair = (f"Lists the functions {shown} (just their names and inputs). Other files that "
                    "#include this file can then use them; the real code is in the matching .c file.",
                    f"A menu of functions other files may use: {shown}.")
            self.blocks.append(self._block("prototypes", "function list", lines, pair))
        else:
            defines = [re.match(r"#define\s+(\w+)\s*(.*)", t.strip()) for t in texts]
            if all(defines):
                shown = ", ".join(f"`{m.group(1)}` = `{m.group(2).strip()}`" for m in defines if m)
                pair = (f"Sets fixed values used in the code: {shown}. The name is replaced by the value "
                        "everywhere in the file.", f"Nicknames for fixed values: {shown}.")
            else:
                pair = explain.actions(texts[:3], self.lang, self.known)
                if len(texts) > 3:
                    pair = (pair[0] + " (and a few more set-up lines)", pair[1])
            self.blocks.append(self._block("setup", "set-up", lines, pair))

    def _imports(self, texts: list[str]) -> tuple[str, str]:
        names = []
        for t in texts:
            m = re.search(r"#include\s*[<\"]([^>\"]+)", t) or re.search(r"^from\s+([\w.]+)", t) \
                or re.search(r"^import\s+(?:static\s+)?([\w.]+)", t) or re.search(r"from\s+['\"]([^'\"]+)", t)
            if m:
                names.append(m.group(1))
        parts = []
        for n in names[:8]:
            what = LIBRARIES.get(n)
            parts.append(f"`{n}`" + (f" ({what})" if what else ""))
        shown = explain._and(parts) or "other files"
        return (f"Brings in code written elsewhere so this file can use it: {shown}.",
                f"Borrow tools from other files: {explain._and([f'`{n}`' for n in names[:8]]) or 'other files'}.")

    @staticmethod
    def _block(kind: str, label: str, lines: list[int], pair: tuple[str, str], function_id: Optional[str] = None) -> dict:
        return {"id": "", "kind": kind, "label": label, "lines": lines, "scope": list(lines),
                "explain": pair[0], "simpler": pair[1], "function_id": function_id}


def _inner(n: Node) -> Node:
    if n.type == "decorated_definition":
        return n.child_by_field_name("definition") or n
    if n.type in ("lexical_declaration", "variable_declaration") and n.named_children:
        value = n.named_children[0].child_by_field_name("value")
        if value is not None and value.type in FUNC_TYPES:
            return value
    return n


def _params(n: Node) -> Optional[Node]:
    n = _inner(n)
    p = n.child_by_field_name("parameters")
    if p is not None:
        return p
    d = n.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        d = d.child_by_field_name("declarator")
    return d.child_by_field_name("parameters") if d is not None else None


def _name_of(n: Node, fb: FileBlocks) -> str:
    inner = _inner(n)
    name = inner.child_by_field_name("name")
    if name is None and inner.type == "type_definition":
        name = inner.child_by_field_name("declarator")
    if name is None and n.type in ("lexical_declaration", "variable_declaration"):
        name = n.named_children[0].child_by_field_name("name")
    if name is None:
        d = inner.child_by_field_name("declarator")
        while d is not None and d.child_by_field_name("declarator") is not None:
            d = d.child_by_field_name("declarator")
        name = d
    return fb.text(name) if name is not None else "?"


def _has_function_declarator(n: Node) -> bool:
    stack = [n]
    while stack:
        x = stack.pop()
        if x.type == "function_declarator":
            return True
        if x.type in ("compound_statement",):
            continue
        stack.extend(x.named_children)
    return False


def _last(n: Node) -> int:
    """Last line of a node (an #include ends at the start of the next line)."""
    row, col = n.end_point
    return row if col == 0 and row > n.start_point[0] else row + 1
