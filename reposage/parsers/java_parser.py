"""Java parser built on Tree-sitter.

What it extracts from a .java file:
  - classes, interfaces, enums, records, their methods and constructors
    (with Javadoc comments)
  - import statements (normal, static and wildcard)
  - method calls and `new X()`, attributed to the enclosing method

Java is statically typed, so we also note the DECLARED TYPE of variables
(fields, parameters, local variables). For `repo.save(user)` where
`UserRepository repo`, the call gets receiver_type="UserRepository", which
lets the graph builder link it to UserRepository.save exactly.
"""

from __future__ import annotations

import posixpath
from typing import Optional

import tree_sitter_java
from tree_sitter import Language, Node

from .base import (Binding, Call, Definition, FileFacts, IdMaker, Import,
                   LanguageParser, doc_comment_before, first_line, text)

TYPE_DECLS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "interface",
}
METHOD_DECLS = ("method_declaration", "constructor_declaration",
                "compact_constructor_declaration")
COMMENTS = ("block_comment", "comment")


class JavaParser(LanguageParser):
    name = "java"
    extensions = (".java",)
    version = 1

    def __init__(self) -> None:
        super().__init__()
        self._lang = Language(tree_sitter_java.language())

    def ts_language(self, path: str) -> Language:
        return self._lang

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, root: Node, facts: FileFacts, ids: IdMaker) -> None:
        self._visit(root, facts, ids, scope=None, var_types={})

    def _visit(self, node: Node, facts: FileFacts, ids: IdMaker,
               scope: Optional[str], var_types: dict[str, str]) -> None:
        """`var_types` maps variable names visible here to their type names."""
        for child in node.children:
            t = child.type

            if t in TYPE_DECLS:
                self._type_decl(child, TYPE_DECLS[t], facts, ids, scope, var_types)
                continue
            if t in METHOD_DECLS:
                self._method(child, facts, ids, scope, var_types)
                continue
            if t == "import_declaration":
                self._import(child, facts)
                continue
            if t == "method_invocation":
                self._call(child, facts, scope, var_types)
            elif t == "object_creation_expression":
                type_name = _type_name(child.child_by_field_name("type"))
                if type_name:
                    facts.calls.append(Call(scope or facts.path, type_name, None,
                                            child.start_point[0] + 1))

            self._visit(child, facts, ids, scope, var_types)

    def _type_decl(self, node: Node, kind: str, facts: FileFacts, ids: IdMaker,
                   scope: Optional[str], outer_vars: dict[str, str]) -> None:
        name = text(node.child_by_field_name("name"))
        class_id = self._add_def(node, name, kind, facts, ids, scope)

        body = node.child_by_field_name("body")
        # Fields (and record components) are visible in every method.
        fields = dict(outer_vars)
        if body is not None:
            fields.update(_declared_vars(body, ("field_declaration",),
                                         descend=False))
            for decl in body.named_children:
                if decl.type == "enum_body_declarations":
                    fields.update(_declared_vars(decl, ("field_declaration",),
                                                 descend=False))
        params = node.child_by_field_name("parameters")   # records
        if params is not None:
            fields.update(_params(params))

        for part in node.children:
            if part.type in ("superclass", "super_interfaces", "extends_interfaces"):
                self._visit(part, facts, ids, scope, outer_vars)
        if body is not None:
            self._visit(body, facts, ids, class_id, fields)

    def _method(self, node: Node, facts: FileFacts, ids: IdMaker,
                scope: Optional[str], class_vars: dict[str, str]) -> None:
        name = text(node.child_by_field_name("name"))
        method_id = self._add_def(node, name, "method", facts, ids, scope)

        visible = dict(class_vars)
        params = node.child_by_field_name("parameters")
        if params is not None:
            visible.update(_params(params))
        body = node.child_by_field_name("body")
        if body is not None:
            visible.update(_declared_vars(
                body, ("local_variable_declaration", "enhanced_for_statement",
                       "resource", "catch_formal_parameter"), descend=True))
            self._visit(body, facts, ids, method_id, visible)

    def _add_def(self, node: Node, name: str, kind: str, facts: FileFacts,
                 ids: IdMaker, scope: Optional[str]) -> str:
        def_id = ids.make(IdMaker.qualified(scope, name))
        facts.definitions.append(Definition(
            id=def_id, name=name, kind=kind, parent=scope,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=_signature(node),
            doc=doc_comment_before(node, COMMENTS),
        ))
        return def_id

    # ---- imports --------------------------------------------------------

    def _import(self, node: Node, facts: FileFacts) -> None:
        """`import a.b.C;`  `import static a.b.C.m;`  `import a.b.*;`"""
        is_static = any(c.type == "static" for c in node.children)
        wildcard = any(c.type == "asterisk" for c in node.children)
        path_node = next((c for c in node.named_children
                          if c.type in ("scoped_identifier", "identifier")), None)
        if path_node is None:
            return
        dotted = text(path_node)
        line = node.start_point[0] + 1

        if wildcard:
            facts.imports.append(Import(source=f"{dotted}.*", line=line))
            if is_static:  # `import static a.b.C.*` -> members of class C
                facts.bindings.append(Binding(local="*", source=dotted, imported="*"))
            return

        if is_static:
            # `import static a.b.C.max` -> the name `max` from class a.b.C
            owner, _, member = dotted.rpartition(".")
            facts.imports.append(Import(source=owner, line=line))
            facts.bindings.append(Binding(local=member, source=owner, imported=member))
        else:
            simple = dotted.rsplit(".", 1)[-1]
            facts.imports.append(Import(source=dotted, line=line))
            facts.bindings.append(Binding(local=simple, source=dotted, imported=simple))

    # ---- calls ----------------------------------------------------------

    def _call(self, node: Node, facts: FileFacts, scope: Optional[str],
              var_types: dict[str, str]) -> None:
        name = text(node.child_by_field_name("name"))
        obj = node.child_by_field_name("object")
        receiver = _receiver(obj)
        rtype = None
        if receiver:
            # `repo.save()` or `this.repo.save()` -> look up repo's declared type
            var = receiver[5:] if receiver.startswith("this.") else receiver
            rtype = var_types.get(var)
        facts.calls.append(Call(scope or facts.path, name, receiver,
                                node.start_point[0] + 1, receiver_type=rtype))

    # ------------------------------------------------------------------
    # Import resolution: "com.shop.model.User" -> ".../com/shop/model/User.java"
    # ------------------------------------------------------------------

    def resolve_import(self, source: str, from_path: str,
                       known_files: set[str]) -> Optional[str]:
        if source.endswith(".*"):
            return None   # a whole package: see resolve_import_many
        parts = source.split(".")
        # Try a.b.C, then a.b (for nested classes like a.b.Outer.Inner).
        for cut in range(len(parts), max(len(parts) - 3, 0), -1):
            rel = "/".join(parts[:cut]) + ".java"
            matches = sorted(f for f in known_files
                             if f == rel or f.endswith("/" + rel))
            if len(matches) == 1:
                return matches[0]
            if matches:
                return None   # ambiguous; better no edge than a wrong one
        return None

    def resolve_import_many(self, source: str, from_path: str,
                            known_files: set[str]) -> list[str]:
        """Like resolve_import, but a wildcard import (`a.b.*`) returns every
        file of that package."""
        if not source.endswith(".*"):
            one = self.resolve_import(source, from_path, known_files)
            return [one] if one else []
        pkg_dir = source[:-2].replace(".", "/")
        in_pkg = sorted(f for f in known_files if f.endswith(".java") and (
            posixpath.dirname(f) == pkg_dir
            or posixpath.dirname(f).endswith("/" + pkg_dir)))
        if in_pkg:
            return in_pkg
        one = self.resolve_import(source[:-2], from_path, known_files)  # static a.b.C.*
        return [one] if one else []


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _signature(node: Node) -> str:
    """Declaration header without annotations, e.g. `public User find(long id)`."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    header = node.text[: end - node.start_byte].decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in header.splitlines()
             if ln.strip() and not ln.strip().startswith("@")]
    sig = " ".join(lines).rstrip("{ ;").strip() if lines else first_line(node)
    return sig if len(sig) <= 200 else sig[:199] + "…"


def _type_name(node: Optional[Node]) -> str:
    """`List<User>` -> "List", `com.x.User` -> "User", `int[]`/`var` -> ""."""
    if node is None:
        return ""
    if node.type == "type_identifier":
        return text(node)
    if node.type == "generic_type":
        return _type_name(node.named_children[0]) if node.named_children else ""
    if node.type == "scoped_type_identifier":
        return text(node).rsplit(".", 1)[-1]
    return ""


def _params(params: Node) -> dict[str, str]:
    found = {}
    for p in params.named_children:
        if p.type in ("formal_parameter", "spread_parameter"):
            name = p.child_by_field_name("name")
            if name is None:   # spread_parameter keeps the name in a declarator
                decl = next((c for c in p.named_children
                             if c.type == "variable_declarator"), None)
                name = decl.child_by_field_name("name") if decl else None
            t = _type_name(p.child_by_field_name("type")
                           or next((c for c in p.named_children
                                    if c.type.endswith("type")
                                    or c.type == "type_identifier"), None))
            if name is not None and t:
                found[text(name)] = t
    return found


def _declared_vars(node: Node, decl_types: tuple[str, ...],
                   descend: bool) -> dict[str, str]:
    """Collect `Type name` declarations (fields or local variables)."""
    found: dict[str, str] = {}
    stack = list(reversed(node.named_children))
    while stack:
        cur = stack.pop()
        if cur.type in decl_types:
            t = _type_name(cur.child_by_field_name("type"))
            if t:
                name = cur.child_by_field_name("name")
                if name is not None:              # for-each, resource, catch
                    found.setdefault(text(name), t)
                for d in cur.children_by_field_name("declarator"):
                    n = d.child_by_field_name("name")
                    if n is not None:
                        found.setdefault(text(n), t)
        # Don't look inside nested classes: their variables are theirs.
        if descend and cur.type not in TYPE_DECLS and cur.type != "class_body":
            stack.extend(reversed(cur.named_children))
    return found


def _receiver(obj: Optional[Node]) -> Optional[str]:
    """`repo` in `repo.save()`, `this` in `this.save()`, `this.repo`, or "?"."""
    if obj is None:
        return None
    if obj.type in ("identifier", "this", "super"):
        return text(obj)
    if obj.type == "field_access":
        inner = _receiver(obj.child_by_field_name("object"))
        if inner and inner != "?":
            return f"{inner}.{text(obj.child_by_field_name('field'))}"
    return "?"
