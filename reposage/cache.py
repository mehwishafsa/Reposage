"""Incremental-scan cache.

We remember, for every file, the content hash we saw last time and the facts
the parser extracted. On the next scan, any file whose hash (and parser
version) is unchanged is taken straight from the cache instead of being
parsed again.

The cache lives in .reposage/cache/ and is machine-local: it is git-ignored
by default. The shareable result is .reposage/graph.json.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .parsers.base import FileFacts

CACHE_FORMAT = 1


class FactsCache:
    def __init__(self, path: str) -> None:
        self.path = path
        self.entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("format") == CACHE_FORMAT:
                self.entries = data.get("files", {})
        except (OSError, ValueError):
            self.entries = {}   # missing or corrupt cache = start fresh

    def get(self, rel: str, digest: str, parser_name: str,
            parser_version: int) -> Optional[FileFacts]:
        """Cached facts, only if the file and parser are both unchanged."""
        e = self.entries.get(rel)
        if (e and e.get("hash") == digest and e.get("parser") == parser_name
                and e.get("parser_version") == parser_version):
            try:
                return FileFacts.from_dict(e["facts"])
            except (KeyError, TypeError):
                return None
        return None

    def save(self, current: dict[str, tuple[str, str, int, FileFacts]]) -> None:
        """Write the cache for the files seen in THIS scan (drops deleted ones).

        `current` maps path -> (hash, parser name, parser version, facts).
        """
        data = {
            "format": CACHE_FORMAT,
            "files": {
                rel: {"hash": h, "parser": p, "parser_version": v,
                      "facts": facts.to_dict()}
                for rel, (h, p, v, facts) in sorted(current.items())
            },
        }
        write_json_atomic(self.path, data, indent=None)


def write_json_atomic(path: str, data: dict, indent: Optional[int] = 2) -> None:
    """Write to a temp file then rename, so a crash never leaves half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=indent, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
