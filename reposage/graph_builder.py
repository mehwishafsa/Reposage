"""Turn per-file facts into one knowledge graph (the content of graph.json).

Input : {path: FileFacts} for every source file in the repo.
Output: a plain dict with nodes (files, classes, functions, ...) and edges
        (contains, imports, calls), ready to be saved as JSON.

The graph is DETERMINISTIC: the same code always gives byte-identical JSON.
To guarantee that we
  - never store timestamps, absolute paths or machine-specific data,
  - sort every list (nodes by id, edges by source/target/type),
  - resolve ambiguous names by rules, never by "whichever came first".

How a call like `auth.login(x)` inside main.py gets linked to a function:
  0. `repo.x()`, repo's type known -> method x of that class (Java)
  1. `self.x()` / `this.x()`      -> method x of the caller's own class
  2. plain `x()`                  -> x defined in an enclosing scope or the same
                                     file, or imported by name into this file
  3. `mod.x()` / `Cls.x()`        -> x inside an imported module or class
  4. fallback for `obj.x()` where obj's type is unknown (and for Java)
                                  -> the ONLY definition named x in the whole
                                     repo (confidence "medium"); if there are
                                     several, we don't guess and skip the call.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import __version__
from .parsers import parser_for
from .parsers.base import CLASS_KINDS, Call, Definition, FileFacts

SCHEMA_VERSION = 1

# Fields filled in later by AI agents (summaries, layers). The parser leaves
# them empty; if a file hasn't changed, previous values are carried over.
AI_FIELDS = ("summary", "layer", "tags")

# Very common method names that usually belong to built-in types
# (`d.get()`, `arr.push()`, `s.split()`...). A call like `x.get()` is NOT
# linked by the name-only fallback, because it would create false edges.
COMMON_METHODS = {
    "get", "set", "add", "append", "extend", "insert", "pop", "push", "shift",
    "remove", "delete", "clear", "copy", "update", "keys", "values", "items",
    "has", "map", "filter", "reduce", "forEach", "find", "some", "every",
    "includes", "indexOf", "index", "count", "sort", "reverse", "slice",
    "splice", "concat", "join", "split", "replace", "strip", "trim", "lower",
    "upper", "format", "startswith", "endswith", "startsWith", "endsWith",
    "toString", "valueOf", "equals", "hashCode", "size", "length", "isEmpty",
    "contains", "then", "catch", "finally", "resolve", "reject", "log",
    "error", "warn", "info", "debug", "read", "write", "open", "close",
    "send", "emit", "on", "off", "call", "apply", "bind", "next", "run",
    "start", "stop", "encode", "decode", "load", "loads", "dump", "dumps",
    "parse", "stringify", "json", "print", "println", "exec", "match",
    "test", "search", "execute", "fetch", "all", "first", "save",
}

SELF_WORDS = {"self", "this", "cls"}

# Receivers that are language/runtime globals: `globalThis.Headers()`,
# `Math.max()`, `System.out.println()`... never code in the repo.
GLOBAL_RECEIVERS = {
    "globalThis", "window", "global", "document", "console", "navigator",
    "Math", "JSON", "Object", "Array", "Promise", "Reflect", "Number",
    "String", "Date", "process", "Buffer", "os", "sys", "System",
}


def build_graph(facts_by_path: dict[str, FileFacts],
                hashes: dict[str, str],
                previous: Optional[dict] = None) -> dict:
    resolver = _Resolver(facts_by_path)
    nodes = _make_nodes(facts_by_path, resolver)
    edges = _make_edges(facts_by_path, resolver)
    _carry_over_ai_fields(nodes, hashes, previous)

    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["source"], e["target"], e["type"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": f"reposage {__version__}",
        "stats": _stats(facts_by_path, nodes, edges),
        "files": {p: {"hash": hashes[p], "language": facts_by_path[p].language}
                  for p in sorted(facts_by_path)},
        "nodes": nodes,
        "edges": edges,
        # Guided tours are written by AI agents later; kept across scans.
        "tours": (previous or {}).get("tours", []),
    }


# ----------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------

def _make_nodes(facts_by_path: dict[str, FileFacts],
                resolver: "_Resolver") -> list[dict]:
    nodes = []
    for path in sorted(facts_by_path):
        f = facts_by_path[path]
        # Imports that point outside the repo (packages, standard library).
        external = sorted({
            imp.source for imp in f.imports
            if imp.source and not resolver.resolve_modules(path, imp.source)
        })
        nodes.append({
            "id": path,
            "type": "file",
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "language": f.language,
            "doc": f.doc,
            "external_imports": external,
            "has_errors": f.has_errors,
            "summary": None,
            "layer": None,
            "tags": [],
        })
        for d in f.definitions:
            nodes.append({
                "id": d.id,
                "type": d.kind,
                "name": d.name,
                "path": path,
                "parent": d.parent,
                "start_line": d.start_line,
                "end_line": d.end_line,
                "signature": d.signature,
                "doc": d.doc,
                "summary": None,
                "layer": None,
                "tags": [],
            })
    return nodes


def _carry_over_ai_fields(nodes: list[dict], hashes: dict[str, str],
                          previous: Optional[dict]) -> None:
    """Keep AI-written summaries for files whose content hasn't changed."""
    if not previous:
        return
    old_hashes = {p: v.get("hash") for p, v in previous.get("files", {}).items()}
    old_nodes = {n["id"]: n for n in previous.get("nodes", [])}
    for node in nodes:
        path = node["path"]
        old = old_nodes.get(node["id"])
        if old is None or old_hashes.get(path) != hashes.get(path):
            continue
        for key in AI_FIELDS:
            if old.get(key) not in (None, "", []):
                node[key] = old[key]


# ----------------------------------------------------------------------
# Edges
# ----------------------------------------------------------------------

def _make_edges(facts_by_path: dict[str, FileFacts],
                resolver: "_Resolver") -> list[dict]:
    # key (source, target, type) -> edge; lets us merge duplicates.
    edges: dict[tuple[str, str, str], dict] = {}

    def add(source: str, target: str, etype: str, **extra) -> None:
        key = (source, target, etype)
        if key not in edges:
            edges[key] = {"source": source, "target": target, "type": etype, **extra}
            return
        old = edges[key]
        # Keep the first line it happens on and the best confidence.
        if "line" in extra and extra["line"] < old.get("line", extra["line"] + 1):
            old["line"] = extra["line"]
        if extra.get("confidence") == "high":
            old["confidence"] = "high"

    for path in sorted(facts_by_path):
        f = facts_by_path[path]

        # contains: file -> top-level definitions, class -> its members
        for d in f.definitions:
            add(d.parent or path, d.id, "contains")

        # imports: file -> file (only files inside the repo)
        for imp in f.imports:
            for target in resolver.resolve_modules(path, imp.source):
                if target != path:
                    add(path, target, "imports", line=imp.line)
        for b in f.bindings:
            # `from pkg import submodule` also depends on pkg/submodule.py
            target = resolver.binding_target(path, b)[0]
            if target and target != path:
                add(path, target, "imports",
                    line=min((i.line for i in f.imports if i.source == b.source),
                             default=1))

        # calls: function -> function
        for call in f.calls:
            hit = resolver.resolve_call(path, call)
            if hit is None:
                continue
            target, confidence = hit
            if target != call.caller:          # ignore direct recursion
                add(call.caller, target, "calls",
                    line=call.line, confidence=confidence)

    return list(edges.values())


class _Resolver:
    """Lookup tables used to link names to definitions across files."""

    def __init__(self, facts_by_path: dict[str, FileFacts]) -> None:
        self.facts = facts_by_path
        self.known_files = set(facts_by_path)
        self.defs: dict[str, Definition] = {}
        self.top_level: dict[str, dict[str, str]] = defaultdict(dict)   # file -> name -> id
        self.members: dict[str, dict[str, str]] = defaultdict(dict)     # def id -> name -> id
        self.by_name: dict[str, list[str]] = defaultdict(list)          # name -> ids

        for path in sorted(facts_by_path):
            for d in facts_by_path[path].definitions:
                self.defs[d.id] = d
                table = self.members[d.parent] if d.parent else self.top_level[path]
                table.setdefault(d.name, d.id)      # first definition wins
                self.by_name[d.name].append(d.id)

        self._module_cache: dict[tuple[str, str], Optional[str]] = {}
        self._bindings: dict[str, dict[str, tuple[Optional[str], str]]] = {}

    # ---- modules ------------------------------------------------------

    def resolve_module(self, from_path: str, source: str) -> Optional[str]:
        key = (from_path, source)
        if key not in self._module_cache:
            parser = parser_for(from_path)
            self._module_cache[key] = parser.resolve_import(
                source, from_path, self.known_files) if parser else None
        return self._module_cache[key]

    def resolve_modules(self, from_path: str, source: str) -> list[str]:
        parser = parser_for(from_path)
        return parser.resolve_import_many(source, from_path, self.known_files) \
            if parser else []

    def binding_target(self, path: str, b) -> tuple[Optional[str], str]:
        """Where an imported name points: (file, name-inside-file or "*")."""
        target = self.resolve_module(path, b.source)
        if b.imported != "*" and (target is None
                                  or b.imported not in self.top_level.get(target, {})):
            # Maybe `from pkg import mod` where mod is a sub-module file.
            parser = parser_for(path)
            sub_fn = getattr(parser, "submodule_spec", None)
            spec = sub_fn(b.source, b.imported) if sub_fn else None
            sub = self.resolve_module(path, spec) if spec else None
            if sub:
                return sub, "*"
        return target, b.imported

    def bindings(self, path: str) -> dict[str, tuple[Optional[str], str]]:
        """local name -> (target file, imported name) for one file."""
        if path not in self._bindings:
            table = {}
            stars = []
            for b in self.facts[path].bindings:
                target, imported = self.binding_target(path, b)
                if b.local == "*":
                    stars.append((target, "*"))
                else:
                    table.setdefault(b.local, (target, imported))
            table["*"] = stars  # type: ignore[assignment]
            self._bindings[path] = table
        return self._bindings[path]

    # ---- calls ----------------------------------------------------------

    def resolve_call(self, path: str, call: Call) -> Optional[tuple[str, str]]:
        """Return (target id, confidence) or None if we can't tell."""
        f = self.facts[path]
        name, recv = call.name, call.receiver

        # 0. the receiver's declared type is known (Java): repo.save()
        if call.receiver_type:
            cls = self._find_class(path, call.receiver_type)
            if cls is None:
                return None   # e.g. `String s; s.charAt(0)` -- not our code
            hit = self.members[cls].get(name)
            if hit:
                return hit, "high"

        # 1. self.x() / this.x()
        if recv in SELF_WORDS:
            cls = self._enclosing_class(call.caller)
            if cls:
                hit = self.members[cls].get(name)
                if hit:
                    return hit, "high"

        # 2. plain x()
        elif recv is None:
            hit = self._lookup_scopes(call.caller, name, f.language == "java")
            if hit:
                return hit, "high"
            hit = self.top_level[path].get(name)
            if hit:
                return hit, "high"
            bound = self.bindings(path).get(name)
            if bound:
                target_file, imported = bound
                if target_file is None:
                    return None     # imported from a library, not our code
                wanted = imported if imported not in ("*", "default") else name
                hit = self._in_file(target_file, imported, name)
                if hit:
                    return hit, "high"
                # e.g. `export default ky` where ky is defined inside a function:
                # accept a unique definition with that name in the target file.
                inside = [d for d in self.by_name.get(wanted, [])
                          if d.split("::", 1)[0] == target_file]
                return (inside[0], "medium") if len(inside) == 1 else None
            for target, _ in self.bindings(path)["*"]:
                if target and name in self.top_level.get(target, {}):
                    return self.top_level[target][name], "high"
            # Python and JS/TS: a bare name can only reach another file
            # through an import, so there's nothing more to try. In Java,
            # classes of the same package are visible without imports.
            if f.language != "java":
                return None

        # 3. mod.x() / Cls.x()
        elif recv != "?":
            bound = self.bindings(path).get(recv) if "." not in recv else None
            if bound and bound[0]:
                target_file, imported = bound
                if imported == "*":                         # a module
                    hit = self.top_level.get(target_file, {}).get(name)
                else:                                       # an imported class
                    cls = self._in_file(target_file, imported, recv)
                    hit = self.members[cls].get(name) if cls else None
                if hit:
                    return hit, "high"
            local_cls = self.top_level[path].get(recv)      # static call on a local class
            if local_cls and self.defs[local_cls].kind in CLASS_KINDS:
                hit = self.members[local_cls].get(name)
                if hit:
                    return hit, "high"
            if f.language == "java" and "." not in recv and recv[:1].isupper():
                # `Helpers.log()`: a class from the same package needs no import.
                cls = self._find_class(path, recv)
                hit = self.members[cls].get(name) if cls else None
                if hit:
                    return hit, "high"

        # 4. fallback: a unique name across the whole repo
        if recv is not None and (name in COMMON_METHODS
                                 or recv.split(".")[0] in GLOBAL_RECEIVERS):
            return None
        candidates = [c for c in self.by_name.get(name, []) if c != call.caller]
        if len(candidates) == 1:
            return candidates[0], "medium"
        return None

    def _in_file(self, target_file: str, name: str, local: str) -> Optional[str]:
        """Find `name` at top level of `target_file`. JS default imports
        (`import X from`) look for a definition called "default" or X."""
        table = self.top_level.get(target_file, {})
        if name == "default":
            return table.get("default") or table.get(local)
        return table.get(name)

    def _find_class(self, path: str, type_name: str) -> Optional[str]:
        """Class called `type_name` as seen from `path`: imported, defined in
        the same file, or the only class with that name in the repo."""
        bound = self.bindings(path).get(type_name)
        if bound and bound[0]:
            hit = self._in_file(bound[0], bound[1], type_name)
            if hit:
                return hit
        hit = self.top_level[path].get(type_name)
        if hit:
            return hit
        classes = [c for c in self.by_name.get(type_name, [])
                   if self.defs[c].kind in CLASS_KINDS]
        return classes[0] if len(classes) == 1 else None

    def _enclosing_class(self, def_id: str) -> Optional[str]:
        cur = self.defs.get(def_id)
        while cur is not None:
            if cur.kind in CLASS_KINDS:
                return cur.id
            cur = self.defs.get(cur.parent) if cur.parent else None
        return None

    def _lookup_scopes(self, def_id: str, name: str,
                       class_members_visible: bool) -> Optional[str]:
        """Look for `name` in the caller and its enclosing definitions.
        In Python/JS a bare name never refers to a class member, in Java it does."""
        cur = self.defs.get(def_id)
        while cur is not None:
            if cur.kind not in CLASS_KINDS or class_members_visible:
                hit = self.members[cur.id].get(name)
                if hit:
                    return hit
            cur = self.defs.get(cur.parent) if cur.parent else None
        return None


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

def _stats(facts_by_path: dict[str, FileFacts], nodes: list[dict],
           edges: list[dict]) -> dict:
    by_lang: dict[str, int] = defaultdict(int)
    for f in facts_by_path.values():
        by_lang[f.language] += 1
    by_node: dict[str, int] = defaultdict(int)
    for n in nodes:
        by_node[n["type"]] += 1
    by_edge: dict[str, int] = defaultdict(int)
    for e in edges:
        by_edge[e["type"]] += 1
    return {
        "files": len(facts_by_path),
        "languages": dict(sorted(by_lang.items())),
        "nodes": dict(sorted(by_node.items())),
        "edges": dict(sorted(by_edge.items())),
    }
