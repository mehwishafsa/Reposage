"""C parser built on Tree-sitter.

What it extracts from a .c / .h file:
  - function definitions (prototypes in headers are only promises, so they
    are skipped - the real function is where its body is)
  - named structs, and `typedef struct {...} Name;`
  - `#include "file.h"` lines (includes with <angle brackets> are system
    libraries like <stdio.h>, so they never point to a file in the repo)
  - every call, attributed to the function it happens in

A comment right above a function (`/* ... */` or a block of `//` lines) is
used as its description, because that is how most C lab programs document
their functions.
"""

from __future__ import annotations

import posixpath
from typing import Optional

import tree_sitter_c
from tree_sitter import Language, Node

from .base import (Binding, Call, Definition, FileFacts, IdMaker, Import,
                   LanguageParser, clean_block_comment, first_line, text, trim_doc)


class CParser(LanguageParser):
    name = "c"
    extensions = (".c", ".h")
    version = 1

    def __init__(self) -> None:
        super().__init__()
        self._lang = Language(tree_sitter_c.language())

    def ts_language(self, path: str) -> Language:
        return self._lang

    # ------------------------------------------------------------------

    def extract(self, root: Node, facts: FileFacts, ids: IdMaker) -> None:
        facts.doc = _file_comment(root)
        self._visit(root, facts, ids, scope=None)

    def _visit(self, node: Node, facts: FileFacts, ids: IdMaker,
               scope: Optional[str]) -> None:
        for child in node.children:
            t = child.type
            if t == "function_definition":
                self._function(child, facts, ids)
                continue
            if t == "preproc_include":
                self._include(child, facts)
                continue
            if t == "struct_specifier" and scope is None:
                self._struct(child, child, facts, ids)
            elif t == "type_definition" and scope is None:
                inner = next((c for c in child.named_children if c.type == "struct_specifier"), None)
                if inner is not None:
                    self._struct(inner, child, facts, ids)
                    continue
            elif t == "call_expression":
                self._call(child, facts, scope)
            self._visit(child, facts, ids, scope)

    def _function(self, node: Node, facts: FileFacts, ids: IdMaker) -> None:
        declarator = _function_declarator(node.child_by_field_name("declarator"))
        name_node = declarator.child_by_field_name("declarator") if declarator else None
        if name_node is None or name_node.type != "identifier":
            return
        name = text(name_node)
        def_id = ids.make(name)
        facts.definitions.append(Definition(
            id=def_id, name=name, kind="function", parent=None,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            signature=first_line(node).rstrip("{").strip(),
            doc=_comment_above(node),
        ))
        body = node.child_by_field_name("body")
        if body is not None:
            self._visit(body, facts, ids, scope=def_id)

    def _struct(self, node: Node, outer: Node, facts: FileFacts, ids: IdMaker) -> None:
        if node.child_by_field_name("body") is None:
            return                                   # `struct Node *next;` is a use, not a definition
        name_node = node.child_by_field_name("name")
        if name_node is None and outer.type == "type_definition":
            name_node = outer.child_by_field_name("declarator")
        if name_node is None:
            return
        name = text(name_node)
        facts.definitions.append(Definition(
            id=ids.make(name), name=name, kind="record", parent=None,
            start_line=outer.start_point[0] + 1, end_line=outer.end_point[0] + 1,
            signature=first_line(outer).rstrip("{").strip(),
            doc=_comment_above(outer),
        ))

    def _include(self, node: Node, facts: FileFacts) -> None:
        path_node = node.child_by_field_name("path")
        if path_node is None or path_node.type != "string_literal":
            return                                   # <stdio.h>: a system library
        source = text(path_node).strip('"')
        facts.imports.append(Import(source=source, line=node.start_point[0] + 1))
        # An include makes every function of that header (and its .c file)
        # visible, like `from ops import *` in Python.
        facts.bindings.append(Binding(local="*", source=source, imported="*"))

    def _call(self, node: Node, facts: FileFacts, scope: Optional[str]) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "identifier":
            name, receiver = text(fn), None
        elif fn.type == "field_expression":          # s.fn(...) / p->fn(...): a function pointer
            field = fn.child_by_field_name("field")
            name, receiver = text(field), text(fn.child_by_field_name("argument")) or "?"
        else:
            return
        facts.calls.append(Call(caller=scope or facts.path, name=name,
                                receiver=receiver, line=node.start_point[0] + 1))

    # ------------------------------------------------------------------

    def resolve_import(self, source: str, from_path: str,
                       known_files: set[str]) -> Optional[str]:
        """`#include "ops.h"`: first next to the including file, then the
        only file in the repo with that name (e.g. include/ops.h)."""
        here = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), source))
        if here in known_files:
            return here
        base = posixpath.basename(source)
        matches = sorted(p for p in known_files if posixpath.basename(p) == base)
        return matches[0] if len(matches) == 1 else None


def _function_declarator(node: Optional[Node]) -> Optional[Node]:
    """`int *add(int a)` wraps the function_declarator in a pointer_declarator."""
    while node is not None and node.type != "function_declarator":
        node = node.child_by_field_name("declarator")
    return node


def _comment_above(node: Node) -> str:
    """The comment block that ends on the line just above `node`."""
    lines: list[str] = []
    expected = node.start_point[0] - 1
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "comment" and prev.end_point[0] == expected:
        raw = text(prev)
        if raw.startswith("/*"):
            lines.insert(0, clean_block_comment(raw))
        else:
            lines.insert(0, raw.lstrip("/").strip())
        expected = prev.start_point[0] - 1
        prev = prev.prev_named_sibling
    return trim_doc("\n".join(lines)) if lines else ""


def _file_comment(root: Node) -> str:
    """A comment at the very top of the file describes the whole program."""
    first = root.named_children[0] if root.named_children else None
    if first is None or first.type != "comment" or first.start_point[0] > 1:
        return ""
    raw = text(first)
    return clean_block_comment(raw) if raw.startswith("/*") else trim_doc(raw.lstrip("/").strip())
