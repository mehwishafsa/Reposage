"""Shared building blocks for every language parser.

Each parser reads ONE file and returns a `FileFacts` object: plain facts
about that file (what it defines, what it imports, what it calls). A parser
never looks at other files. Linking files together is the job of
`graph_builder.py`, which runs after all files are parsed.

Keeping per-file facts separate from the cross-file graph is what makes
incremental scans possible: if a file's content hash hasn't changed, its
FileFacts can be reused from the cache without parsing it again.
"""

from __future__ import annotations

import inspect
import posixpath
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from tree_sitter import Language, Node, Parser


# Definition kinds that can contain methods.
CLASS_KINDS = ("class", "interface", "enum", "record")


# --------------------------------------------------------------------------
# The facts a parser extracts from one file
# --------------------------------------------------------------------------

@dataclass
class Definition:
    """A class, function, method or interface defined in a file."""
    id: str                 # stable ID, e.g. "src/auth.py::User.login"
    name: str               # short name, e.g. "login"
    kind: str               # "class" | "interface" | "enum" | "record" | "function" | "method"
    parent: Optional[str]   # ID of the enclosing definition (None = top level)
    start_line: int         # 1-based, inclusive
    end_line: int
    signature: str          # first line of the definition, trimmed
    doc: str                # docstring / doc comment ("" if none)


@dataclass
class Import:
    """One import statement, e.g. `from .db import get_user`."""
    source: str             # module or path as written: "db", "./utils", "java.util.List"
    line: int


@dataclass
class Binding:
    """A local name that an import makes available in this file.

    `from db import get_user as gu`  ->  Binding(local="gu", source="db", imported="get_user")
    `import numpy as np`             ->  Binding(local="np", source="numpy", imported="*")

    imported="*" means the local name refers to the whole module.
    """
    local: str
    source: str
    imported: str


@dataclass
class Call:
    """A function/method call found inside some definition (or at file level)."""
    caller: str             # ID of the enclosing definition, or the file path
    name: str               # the called name: `login` in `auth.login(x)`
    receiver: Optional[str] # `auth` in `auth.login(x)`; None for plain `login(x)`
    line: int
    # Declared type of the receiver when the language tells us (Java:
    # `UserRepo repo; repo.save()` -> "UserRepo"). None if unknown.
    receiver_type: Optional[str] = None


@dataclass
class FileFacts:
    """Everything one parser learned about one file."""
    path: str
    language: str
    doc: str = ""
    has_errors: bool = False   # True if Tree-sitter hit a syntax error
    definitions: list[Definition] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    # -- (de)serialisation, used by the incremental cache --
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FileFacts":
        return cls(
            path=d["path"],
            language=d["language"],
            doc=d.get("doc", ""),
            has_errors=d.get("has_errors", False),
            definitions=[Definition(**x) for x in d["definitions"]],
            imports=[Import(**x) for x in d["imports"]],
            bindings=[Binding(**x) for x in d["bindings"]],
            calls=[Call(**x) for x in d["calls"]],
        )


# --------------------------------------------------------------------------
# Base class for language parsers
# --------------------------------------------------------------------------

class LanguageParser:
    """Subclasses set the class attributes and implement `extract`."""

    name: str = ""                     # e.g. "python"
    extensions: tuple[str, ...] = ()   # e.g. (".py",)
    # Bump this when a parser's output changes, so cached results are redone.
    version: int = 1

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    # Subclasses return the tree-sitter Language for a given file path.
    def ts_language(self, path: str) -> Language:
        raise NotImplementedError

    def parse(self, path: str, source: bytes) -> FileFacts:
        """Parse `source` (the file's bytes) and return its facts."""
        lang = self.ts_language(path)
        key = str(id(lang))
        if key not in self._parsers:
            self._parsers[key] = Parser(lang)
        tree = self._parsers[key].parse(source)
        facts = FileFacts(path=path, language=self.name,
                          has_errors=tree.root_node.has_error)
        self.extract(tree.root_node, facts, IdMaker(path))
        return facts

    def extract(self, root: Node, facts: FileFacts, ids: "IdMaker") -> None:
        raise NotImplementedError

    def resolve_import(self, source: str, from_path: str,
                       known_files: set[str]) -> Optional[str]:
        """Map an import string to a file in the repo, or None if external."""
        return None

    def resolve_import_many(self, source: str, from_path: str,
                            known_files: set[str]) -> list[str]:
        """All repo files an import refers to. Usually 0 or 1; a Java
        wildcard import (`import a.b.*`) can refer to a whole package."""
        one = self.resolve_import(source, from_path, known_files)
        return [one] if one else []


# --------------------------------------------------------------------------
# Small helpers shared by the parsers
# --------------------------------------------------------------------------

class IdMaker:
    """Builds stable, unique IDs for definitions within one file.

    IDs look like  "path/to/file.py::Class.method".
    If the same name appears twice in the same scope (Java overloads, or a
    Python function redefined later), the later ones get "#2", "#3", ...
    Because files are always walked in the same order, IDs are deterministic.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._seen: dict[str, int] = {}

    def make(self, qualified_name: str) -> str:
        base = f"{self.path}::{qualified_name}"
        count = self._seen.get(base, 0) + 1
        self._seen[base] = count
        return base if count == 1 else f"{base}#{count}"

    @staticmethod
    def qualified(parent_id: Optional[str], name: str) -> str:
        """"file::A" + "b" -> "A.b";  None + "b" -> "b"."""
        if parent_id is None:
            return name
        parent_q = parent_id.split("::", 1)[1].split("#", 1)[0]
        return f"{parent_q}.{name}"


def text(node: Optional[Node]) -> str:
    """The source text of a node (or "" for None)."""
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def first_line(node: Node, limit: int = 200) -> str:
    """First line of a node's text, e.g. `def login(username, password):`."""
    line = text(node).splitlines()[0].strip() if node.text else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def clean_block_comment(raw: str) -> str:
    """Turn `/** Foo.\n * Bar. */` into `Foo.\nBar.`."""
    body = raw.strip()
    if body.startswith("/**"):
        body = body[3:]
    elif body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln[1:].strip() if ln.startswith("*") else ln for ln in lines]
    return trim_doc("\n".join(lines))


def doc_comment_before(node: Node, comment_types: Iterable[str]) -> str:
    """Return the `/** ... */` comment right above `node`, if any."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type in comment_types:
        raw = text(prev)
        # Must be a doc comment and must end on the line just above the node.
        if raw.startswith("/**") and prev.end_point[0] >= node.start_point[0] - 1:
            return clean_block_comment(raw)
    return ""


def trim_doc(doc: str, limit: int = 1000) -> str:
    """Normalise indentation and cap length so graph.json stays small."""
    doc = inspect.cleandoc(doc).strip()
    return doc if len(doc) <= limit else doc[: limit - 1] + "…"


def join_relative(from_path: str, spec: str) -> str:
    """Resolve "./x" or "../x" against the directory of `from_path`."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
