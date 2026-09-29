"""JavaScript + TypeScript parser built on Tree-sitter.

Handles .js .jsx .mjs .cjs (JavaScript grammar, which includes JSX),
.ts .mts .cts (TypeScript grammar) and .tsx (TSX grammar).

What it extracts:
  - function declarations, `const f = () => {}` / `const f = function () {}`
  - `exports.f = function () {}` style CommonJS functions
  - classes, their methods and arrow-function fields, TS interfaces
  - ES `import` / `export ... from` statements and `require()` calls
  - every call and `new X()`, attributed to the enclosing function/method
"""

from __future__ import annotations

import posixpath
from typing import Optional

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node

from .base import (Binding, Call, Definition, FileFacts, IdMaker, Import,
                   LanguageParser, doc_comment_before, first_line,
                   join_relative, text)

JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")
TS_EXTS = (".ts", ".mts", ".cts")
TSX_EXTS = (".tsx",)

# When resolving `import "./utils"`, try these endings in this order.
RESOLVE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

FUNCTION_VALUES = ("arrow_function", "function_expression", "function",
                   "generator_function")
CLASS_DECLS = ("class_declaration", "abstract_class_declaration", "class")


class JavaScriptParser(LanguageParser):
    name = "javascript"
    extensions = JS_EXTS + TS_EXTS + TSX_EXTS
    version = 1

    def __init__(self) -> None:
        super().__init__()
        self._js = Language(tree_sitter_javascript.language())
        self._ts = Language(tree_sitter_typescript.language_typescript())
        self._tsx = Language(tree_sitter_typescript.language_tsx())

    def ts_language(self, path: str) -> Language:
        if path.endswith(TSX_EXTS):
            return self._tsx
        if path.endswith(TS_EXTS):
            return self._ts
        return self._js

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, root: Node, facts: FileFacts, ids: IdMaker) -> None:
        if facts.path.endswith(TS_EXTS + TSX_EXTS):
            facts.language = "typescript"
        self._visit(root, facts, ids, scope=None)

    def _visit(self, node: Node, facts: FileFacts, ids: IdMaker,
               scope: Optional[str]) -> None:
        for child in node.children:
            t = child.type

            # --- definitions -------------------------------------------
            if t in ("function_declaration", "generator_function_declaration"):
                self._function(child, _name_of(child), child, facts, ids, scope)
                continue
            if t in CLASS_DECLS and child.child_by_field_name("body") is not None:
                self._class(child, facts, ids, scope)
                continue
            if t == "interface_declaration":
                self._add_def(child, text(child.child_by_field_name("name")),
                              "interface", child, facts, ids, scope)
                continue
            if t == "variable_declarator":
                if self._variable(child, facts, ids, scope):
                    continue
            if t == "assignment_expression":
                if self._assignment(child, facts, ids, scope):
                    continue

            # --- imports -----------------------------------------------
            if t == "import_statement":
                self._import(child, facts)
                continue
            if t == "export_statement" and child.child_by_field_name("source"):
                # `export { a } from "./x"` / `export * from "./x"`
                facts.imports.append(Import(
                    source=_string_value(child.child_by_field_name("source")),
                    line=child.start_point[0] + 1))
                continue

            # --- calls -------------------------------------------------
            if t == "call_expression":
                self._call(child, facts, scope)
            elif t == "new_expression":
                ctor = child.child_by_field_name("constructor")
                if ctor is not None and ctor.type in ("identifier", "member_expression"):
                    name, receiver = _callee(ctor)
                    if name:
                        facts.calls.append(Call(scope or facts.path, name, receiver,
                                                child.start_point[0] + 1))

            self._visit(child, facts, ids, scope)

    # ---- definition helpers -------------------------------------------

    def _add_def(self, node: Node, name: str, kind: str, doc_anchor: Node,
                 facts: FileFacts, ids: IdMaker, scope: Optional[str]) -> str:
        """Record one definition and return its ID."""
        def_id = ids.make(IdMaker.qualified(scope, name))
        facts.definitions.append(Definition(
            id=def_id, name=name, kind=kind, parent=scope,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=first_line(node),
            doc=_doc_for(doc_anchor),
        ))
        return def_id

    def _function(self, fn: Node, name: str, doc_anchor: Node,
                  facts: FileFacts, ids: IdMaker, scope: Optional[str],
                  kind: str = "function") -> None:
        def_id = self._add_def(fn, name, kind, doc_anchor, facts, ids, scope)
        # Walk params (default values) and body with the new scope.
        self._visit(fn, facts, ids, def_id)

    def _class(self, node: Node, facts: FileFacts, ids: IdMaker,
               scope: Optional[str], name: Optional[str] = None,
               doc_anchor: Optional[Node] = None) -> None:
        name = name or _name_of(node)
        class_id = self._add_def(node, name, "class", doc_anchor or node,
                                 facts, ids, scope)
        # `extends Base(...)` etc. belong to the outer scope.
        for c in node.children:
            if c.type in ("class_heritage",):
                self._visit(c, facts, ids, scope)

        body = node.child_by_field_name("body")
        for member in body.named_children if body else []:
            if member.type == "method_definition":
                self._function(member, text(member.child_by_field_name("name")),
                               member, facts, ids, class_id, kind="method")
            elif member.type in ("field_definition", "public_field_definition"):
                value = member.child_by_field_name("value")
                field_name = member.child_by_field_name("name") \
                    or member.child_by_field_name("property")
                if value is not None and value.type in FUNCTION_VALUES:
                    # `handleClick = () => {...}` is effectively a method.
                    self._function(value, text(field_name), member,
                                   facts, ids, class_id, kind="method")
                else:
                    self._visit(member, facts, ids, class_id)
            else:
                self._visit(member, facts, ids, class_id)

    def _variable(self, decl: Node, facts: FileFacts, ids: IdMaker,
                  scope: Optional[str]) -> bool:
        """`const f = () => {}`, `const C = class {}`, `const x = require("y")`.
        Returns True if the node was fully handled."""
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if value is None or name_node is None:
            return False
        anchor = _statement_of(decl)
        if value.type in FUNCTION_VALUES and name_node.type == "identifier":
            self._function(value, text(name_node), anchor, facts, ids, scope)
            return True
        if value.type == "class" and name_node.type == "identifier":
            self._class(value, facts, ids, scope, name=text(name_node),
                        doc_anchor=anchor)
            return True
        if _is_require(value):
            self._require(name_node, value, facts)
            return False  # still walk it so the call itself is recorded
        return False

    def _assignment(self, node: Node, facts: FileFacts, ids: IdMaker,
                    scope: Optional[str]) -> bool:
        """`exports.f = function () {}` / `Foo.prototype.bar = () => {}`."""
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None or right.type not in FUNCTION_VALUES:
            return False
        if left.type != "member_expression":
            return False
        name = text(left.child_by_field_name("property"))
        self._function(right, name, _statement_of(node), facts, ids, scope)
        return True

    # ---- imports --------------------------------------------------------

    def _import(self, node: Node, facts: FileFacts) -> None:
        source = _string_value(node.child_by_field_name("source"))
        facts.imports.append(Import(source=source, line=node.start_point[0] + 1))
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            return  # side-effect import: `import "./styles.css"`
        for part in clause.named_children:
            if part.type == "identifier":                       # import X from
                facts.bindings.append(Binding(text(part), source, "default"))
            elif part.type == "namespace_import":               # import * as X
                ident = next((c for c in part.named_children if c.type == "identifier"), None)
                if ident is not None:
                    facts.bindings.append(Binding(text(ident), source, "*"))
            elif part.type == "named_imports":                  # import { a as b }
                for spec in part.named_children:
                    if spec.type != "import_specifier":
                        continue
                    imported = text(spec.child_by_field_name("name"))
                    alias = spec.child_by_field_name("alias")
                    facts.bindings.append(Binding(
                        text(alias) if alias else imported, source, imported))

    def _require(self, target: Node, call: Node, facts: FileFacts) -> None:
        """`const x = require("m")` or `const { a, b: c } = require("m")`."""
        source = _string_value(call.child_by_field_name("arguments").named_children[0])
        facts.imports.append(Import(source=source, line=call.start_point[0] + 1))
        if target.type == "identifier":
            facts.bindings.append(Binding(text(target), source, "*"))
        elif target.type == "object_pattern":
            for prop in target.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    facts.bindings.append(Binding(text(prop), source, text(prop)))
                elif prop.type == "pair_pattern":
                    key = text(prop.child_by_field_name("key"))
                    val = prop.child_by_field_name("value")
                    if val is not None and val.type == "identifier":
                        facts.bindings.append(Binding(text(val), source, key))

    # ---- calls ----------------------------------------------------------

    def _call(self, node: Node, facts: FileFacts, scope: Optional[str]) -> None:
        func = node.child_by_field_name("function")
        if func is None or func.type in ("import", "super"):
            return
        name, receiver = _callee(func)
        if name:
            facts.calls.append(Call(scope or facts.path, name, receiver,
                                    node.start_point[0] + 1))

    # ------------------------------------------------------------------
    # Import resolution: "./utils" -> "src/utils.ts"
    # ------------------------------------------------------------------

    def resolve_import(self, source: str, from_path: str,
                       known_files: set[str]) -> Optional[str]:
        if not source.startswith("."):
            return None  # a package from node_modules (external)
        base = join_relative(from_path, source)
        candidates = [base]
        # TS projects often write `import "./x.js"` while the file is x.ts.
        stem, ext = posixpath.splitext(base)
        if ext in JS_EXTS:
            candidates += [stem + e for e in RESOLVE_EXTS]
        candidates += [base + e for e in RESOLVE_EXTS]
        candidates += [f"{base}/index{e}" for e in RESOLVE_EXTS]
        for cand in candidates:
            if cand in known_files:
                return cand
        return None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _name_of(node: Node) -> str:
    name = node.child_by_field_name("name")
    return text(name) if name is not None else "default"  # `export default function () {}`


def _statement_of(node: Node) -> Node:
    """Climb from a declarator to its statement (where the doc comment sits)."""
    cur = node
    while cur.parent is not None and cur.parent.type in (
            "lexical_declaration", "variable_declaration", "expression_statement",
            "export_statement", "assignment_expression"):
        cur = cur.parent
    return cur


def _doc_for(node: Node) -> str:
    """Doc comment above the node, or above its `export` wrapper."""
    anchor = node
    if anchor.parent is not None and anchor.parent.type == "export_statement":
        anchor = anchor.parent
    return doc_comment_before(anchor, ("comment",))


def _string_value(node: Optional[Node]) -> str:
    return text(node).strip("'\"`") if node is not None else ""


def _is_require(value: Node) -> bool:
    if value.type != "call_expression":
        return False
    func = value.child_by_field_name("function")
    args = value.child_by_field_name("arguments")
    return (func is not None and text(func) == "require" and args is not None
            and len(args.named_children) == 1
            and args.named_children[0].type == "string")


def _callee(func: Node) -> tuple[str, Optional[str]]:
    """Split a call target into (name, receiver):
    `foo`        -> ("foo", None)
    `api.get`    -> ("get", "api")
    `this.save`  -> ("save", "this")
    """
    if func.type == "identifier":
        return text(func), None
    if func.type == "member_expression":
        prop = func.child_by_field_name("property")
        return text(prop), _simple_receiver(func.child_by_field_name("object"))
    return "", None


def _simple_receiver(obj: Optional[Node]) -> Optional[str]:
    if obj is None:
        return None
    if obj.type in ("identifier", "this"):
        return text(obj)
    if obj.type == "member_expression":
        inner = _simple_receiver(obj.child_by_field_name("object"))
        if inner and inner != "?":
            return f"{inner}.{text(obj.child_by_field_name('property'))}"
    return "?"
