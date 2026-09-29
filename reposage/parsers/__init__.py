"""Language registry: picks the right parser for a file by its extension.

To add a language, write a `LanguageParser` subclass and add it to
`_PARSER_CLASSES` below.
"""

from __future__ import annotations

from typing import Optional

from .base import FileFacts, LanguageParser
from .javascript_parser import JavaScriptParser
from .python_parser import PythonParser

_PARSER_CLASSES = [PythonParser, JavaScriptParser]

_by_ext: dict[str, LanguageParser] = {}
_by_name: dict[str, LanguageParser] = {}


def _load() -> None:
    """Create each parser once (loading grammars is not free)."""
    if _by_ext:
        return
    for cls in _PARSER_CLASSES:
        parser = cls()
        _by_name[parser.name] = parser
        for ext in parser.extensions:
            _by_ext[ext] = parser


def parser_for(path: str) -> Optional[LanguageParser]:
    """Return the parser for this file, or None if the language is unsupported."""
    _load()
    dot = path.rfind(".")
    return _by_ext.get(path[dot:].lower()) if dot != -1 else None


def supported_extensions() -> set[str]:
    _load()
    return set(_by_ext)


def all_parsers() -> list[LanguageParser]:
    _load()
    return [_by_name[name] for name in sorted(_by_name)]


__all__ = ["FileFacts", "LanguageParser", "parser_for",
           "supported_extensions", "all_parsers"]
