"""Python parser built on Tree-sitter.

What it extracts from a .py file:
  - classes, functions and methods (with line numbers and docstrings)
  - import statements and the names they bind
  - every call, attributed to the function/method it happens in
"""

from __future__ import annotations

import ast
import posixpath
from typing import Optional

import tree_sitter_python
from tree_sitter import Language, Node

from .base import (Binding, Call, Definition, FileFacts, IdMaker, Import,
                   LanguageParser, first_line, text, trim_doc)


class PythonParser(LanguageParser):
    name = "python"
    extensions = (".py", ".pyi")
    version = 2   # 2: skip @overload stubs

    def __init__(self) -> None:
        super().__init__()
        self._lang = Language(tree_sitter_python.language())

    def ts_language(self, path: str) -> Language:
        return self._lang

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, root: Node, facts: FileFacts, ids: IdMaker) -> None:
        facts.doc = _docstring(root)
        self._visit(root, facts, ids, scope=None, scope_kind=None)

    def _visit(self, node: Node, facts: FileFacts, ids: IdMaker,
               scope: Optional[str], scope_kind: Optional[str]) -> None:
        """Walk the tree depth-first. `scope` is the ID of the enclosing
        class/function (None at module level)."""
        for child in node.children:
            t = child.type

            if t == "decorated_definition":
                # `@decorator` + def/class: the decorators may contain calls,
                # and the wrapped definition is in the `definition` field.
                for dec in child.children_by_field_name("decorator") or [
                        c for c in child.children if c.type == "decorator"]:
                    self._visit(dec, facts, ids, scope, scope_kind)
                inner = child.child_by_field_name("definition")
                if _is_overload_stub(child):
                    continue  # `@overload` stubs are type hints, not real code
                if inner is not None:
                    self._definition(inner, child, facts, ids, scope, scope_kind)
                continue

            if t in ("function_definition", "class_definition"):
                self._definition(child, child, facts, ids, scope, scope_kind)
                continue

            if t == "import_statement":
                self._import(child, facts)
            elif t == "import_from_statement":
                self._import_from(child, facts)
            elif t == "call":
                self._call(child, facts, scope)

            # Keep walking into this node's children (e.g. calls nested
            # inside arguments, if-blocks, loops...).
            self._visit(child, facts, ids, scope, scope_kind)

    def _definition(self, node: Node, outer: Node, facts: FileFacts,
                    ids: IdMaker, scope: Optional[str],
                    scope_kind: Optional[str]) -> None:
        """Record a class or function, then walk its body."""
        name = text(node.child_by_field_name("name"))
        if node.type == "class_definition":
            kind = "class"
        elif scope_kind == "class":
            kind = "method"          # a def directly inside a class body
        else:
            kind = "function"        # top-level, or nested inside a function

        def_id = ids.make(IdMaker.qualified(scope, name))
        facts.definitions.append(Definition(
            id=def_id,
            name=name,
            kind=kind,
            parent=scope,
            # `outer` includes decorators, so line numbers cover them too.
            start_line=outer.start_point[0] + 1,
            end_line=outer.end_point[0] + 1,
            signature=first_line(node),
            doc=_docstring(node.child_by_field_name("body")),
        ))

        # Default values and base classes are evaluated in the *outer* scope.
        for field_name in ("parameters", "superclasses"):
            part = node.child_by_field_name(field_name)
            if part is not None:
                self._visit(part, facts, ids, scope, scope_kind)

        body = node.child_by_field_name("body")
        if body is not None:
            self._visit(body, facts, ids, def_id,
                        "class" if kind == "class" else "function")

    # ---- imports -------------------------------------------------------

    def _import(self, node: Node, facts: FileFacts) -> None:
        """`import a.b` / `import a.b as c`"""
        line = node.start_point[0] + 1
        for item in node.children_by_field_name("name"):
            if item.type == "aliased_import":
                module = text(item.child_by_field_name("name"))
                local = text(item.child_by_field_name("alias"))
            else:
                module = text(item)
                local = module.split(".")[0]   # `import a.b` binds `a`
            facts.imports.append(Import(source=module, line=line))
            # Only `import a` / `import a.b as c` give a direct handle to the module.
            if item.type == "aliased_import" or "." not in module:
                facts.bindings.append(Binding(local=local, source=module, imported="*"))

    def _import_from(self, node: Node, facts: FileFacts) -> None:
        """`from x import a, b as c` / `from . import y` / `from x import *`"""
        line = node.start_point[0] + 1
        module = text(node.child_by_field_name("module_name"))
        facts.imports.append(Import(source=module, line=line))
        for item in node.children_by_field_name("name"):
            if item.type == "aliased_import":
                name = text(item.child_by_field_name("name"))
                local = text(item.child_by_field_name("alias"))
            else:
                name = local = text(item)
            facts.bindings.append(Binding(local=local, source=module, imported=name))
        if any(c.type == "wildcard_import" for c in node.children):
            # `from x import *`: local="*" tells the graph builder to look
            # up otherwise-unknown names inside module x.
            facts.bindings.append(Binding(local="*", source=module, imported="*"))

    # ---- calls ---------------------------------------------------------

    def _call(self, node: Node, facts: FileFacts, scope: Optional[str]) -> None:
        func = node.child_by_field_name("function")
        if func is None:
            return
        if func.type == "identifier":
            name, receiver = text(func), None
        elif func.type == "attribute":
            name = text(func.child_by_field_name("attribute"))
            receiver = _simple_receiver(func.child_by_field_name("object"))
        else:
            return  # e.g. `funcs[0]()` or `(lambda: x)()` -- no static name
        facts.calls.append(Call(caller=scope or facts.path, name=name,
                                receiver=receiver, line=node.start_point[0] + 1))

    # ------------------------------------------------------------------
    # Import resolution: "a.b" / ".x" -> a file path in the repo
    # ------------------------------------------------------------------

    def resolve_import(self, source: str, from_path: str,
                       known_files: set[str]) -> Optional[str]:
        if not source:
            return None
        here = posixpath.dirname(from_path)

        if source.startswith("."):
            # Relative import: one dot = this package, two = parent, ...
            dots = len(source) - len(source.lstrip("."))
            base = here
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            rest = source[dots:].replace(".", "/")
            return _module_file(posixpath.join(base, rest) if rest else base,
                                known_files)

        rel = source.replace(".", "/")
        # 1) next to the importing file (script-style projects)
        # 2) from the repo root
        for base in (here, ""):
            found = _module_file(posixpath.join(base, rel) if base else rel,
                                 known_files)
            if found:
                return found
        # 3) somewhere deeper, e.g. src/<package>/... -- only if unambiguous
        matches = sorted(f for f in known_files
                         if f.endswith(f"/{rel}.py") or f.endswith(f"/{rel}/__init__.py"))
        return matches[0] if len(matches) == 1 else None

    def submodule_spec(self, source: str, imported: str) -> Optional[str]:
        """`from pkg import mod` may import a sub-module: "pkg" + "mod" -> "pkg.mod"."""
        if imported == "*":
            return None
        return source + imported if source.endswith(".") else f"{source}.{imported}"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _module_file(path_no_ext: str, known_files: set[str]) -> Optional[str]:
    """"a/b" -> "a/b.py" or "a/b/__init__.py" if either exists."""
    path_no_ext = path_no_ext.strip("/")
    for cand in (f"{path_no_ext}.py", f"{path_no_ext}/__init__.py", f"{path_no_ext}.pyi"):
        cand = cand.lstrip("/")
        if cand in known_files:
            return cand
    return None


def _docstring(body: Optional[Node]) -> str:
    """The docstring is a string literal as the first statement of a body."""
    if body is None:
        return ""
    for child in body.named_children:
        if child.type == "comment":
            continue
        if child.type == "expression_statement" and child.named_children \
                and child.named_children[0].type == "string":
            raw = text(child.named_children[0])
            try:
                value = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                value = raw.strip("\"'rRbBuUfF")
            return trim_doc(value) if isinstance(value, str) else ""
        return ""  # first statement is not a string -> no docstring
    return ""


def _is_overload_stub(decorated: Node) -> bool:
    """True for `@overload` / `@typing.overload` definitions."""
    return any(text(c).lstrip("@").strip() in ("overload", "typing.overload",
                                                "t.overload")
               for c in decorated.children if c.type == "decorator")


def _simple_receiver(obj: Optional[Node]) -> Optional[str]:
    """`auth` in `auth.login()`, `self.db` in `self.db.get()`, else "?"."""
    if obj is None:
        return None
    if obj.type == "identifier":
        return text(obj)
    if obj.type == "attribute":
        inner = _simple_receiver(obj.child_by_field_name("object"))
        if inner and inner != "?":
            return f"{inner}.{text(obj.child_by_field_name('attribute'))}"
    return "?"   # something complex like `get_db().query()`
