"""Find language constructs (for loops, if statements, switch, ...) in a
project's code, straight from the syntax trees.

Plain word search can't do this: "for" and "if" are everywhere in English
and are ignored as stop words. So when a student asks about "the for loop",
we look for real `for_statement` nodes instead, and know exactly which
function and lines they are in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from tree_sitter import Parser

from .. import config  # noqa: F401  (puts the reposage engine on sys.path)
from reposage.parsers import parser_for

# syntax-tree node types per construct kind (all five languages together)
NODE_KINDS: dict[str, set[str]] = {
    "for_loop": {"for_statement", "for_in_statement", "enhanced_for_statement"},
    "while_loop": {"while_statement"},
    "do_while": {"do_statement"},
    "if": {"if_statement"},
    "switch": {"switch_statement", "switch_expression", "match_statement"},
    "return": {"return_statement"},
    "break": {"break_statement"},
    "continue": {"continue_statement"},
    "array": {"array_declarator", "array_creation_expression", "array", "list"},
    "pointer": {"pointer_declarator", "pointer_expression"},
    "struct": {"struct_specifier"},
    "include": {"preproc_include"},
}
CALL_KINDS = {"print": {"printf", "puts", "print", "println", "log"},
              "input": {"scanf", "input", "gets", "fgets", "nextInt", "nextLine", "readline"}}
LABELS = {"for_loop": "for loop", "while_loop": "while loop", "do_while": "do-while loop", "if": "if statement",
          "switch": "switch", "return": "return", "break": "break", "continue": "continue", "array": "array",
          "pointer": "pointer", "struct": "struct", "include": "#include", "print": "printf/print call",
          "input": "scanf/input call", "function": "function", "declaration": "variable", "recursion": "recursive call"}


@dataclass
class Found:
    kind: str
    file: str
    start: int            # first line of the construct
    end: int              # last line
    header_end: int       # last line of its header (e.g. the `for (...)` part)
    code: str             # its first line, trimmed
    function_id: Optional[str]
    function_name: Optional[str]


class ConstructIndex:
    """All constructs of a project, found once and kept in memory."""

    def __init__(self, src_dir: str, graph: dict):
        self.src = src_dir
        self.graph = graph
        self.funcs = sorted((n for n in graph["nodes"] if n["type"] in ("function", "method")),
                            key=lambda n: (n["path"], n["start_line"]))
        self.items: list[Found] = []
        for path in sorted(graph["files"]):
            self._scan(path)

    def _scan(self, path: str) -> None:
        parser = parser_for(path)
        if parser is None:
            return
        try:
            with open(os.path.join(self.src, path), "rb") as fh:
                source = fh.read()
        except OSError:
            return
        root = Parser(parser.ts_language(path)).parse(source).root_node
        lines = source.decode("utf-8", "replace").splitlines()
        stack = [root]
        while stack:
            node = stack.pop()
            kind = next((k for k, types in NODE_KINDS.items() if node.type in types), None)
            if kind == "struct" and node.child_by_field_name("body") is None:
                kind = None
            if kind == "array" and node.type == "list" and parser.name != "python":
                kind = None
            if node.type in ("call_expression", "call", "method_invocation"):
                fn = node.child_by_field_name("function") or node.child_by_field_name("name")
                name = fn.text.decode("utf-8", "replace").rsplit(".", 1)[-1] if fn is not None else ""
                kind = next((k for k, names in CALL_KINDS.items() if name in names), None)
            if kind:
                start, end = node.start_point[0] + 1, node.end_point[0] + 1
                body = node.child_by_field_name("body") or node.child_by_field_name("consequence")
                header_end = body.start_point[0] + 1 if body is not None else start
                f = self.function_at(path, start)
                self.items.append(Found(kind, path, start, end, max(start, header_end),
                                        lines[start - 1].strip()[:90] if start - 1 < len(lines) else "",
                                        f["id"] if f else None, f["name"] if f else None))
            stack.extend(reversed(node.named_children))
        self.items.sort(key=lambda x: (x.file, x.start, x.kind))

    def function_at(self, path: str, line: int) -> Optional[dict]:
        best = None
        for f in self.funcs:
            if f["path"] == path and f["start_line"] <= line <= f["end_line"]:
                if best is None or f["start_line"] >= best["start_line"]:
                    best = f              # the innermost function
        return best

    def find(self, kinds: tuple[str, ...] | list[str]) -> list[Found]:
        out = []
        for kind in kinds:
            if kind == "function":
                out += [Found("function", f["path"], f["start_line"], f["end_line"], f["start_line"],
                              f.get("signature", f["name"])[:90], f["id"], f["name"]) for f in self.funcs]
            elif kind == "declaration":
                continue          # every function has variables; the function examples cover it
            elif kind == "recursion":
                selfcalls = {e["source"] for e in self.graph["edges"]
                             if e["type"] == "calls" and e["source"] == e["target"]}
                out += [Found("recursion", f["path"], f["start_line"], f["end_line"], f["start_line"],
                              f.get("signature", f["name"])[:90], f["id"], f["name"])
                        for f in self.funcs if f["id"] in selfcalls]
            else:
                out += [x for x in self.items if x.kind == kind]
        return out
