"""Find the source files to analyse and fingerprint them.

File discovery order:
  1. If the folder is inside a git repo, ask git for the file list. This
     automatically respects .gitignore (no node_modules, build output, ...).
  2. Otherwise walk the folder ourselves, skipping well-known junk folders.

Paths are always returned sorted and with forward slashes, so the same repo
produces the same list on Linux, macOS and Windows.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

# Folders we never want to analyse, even without a .gitignore.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".reposage", "node_modules", "bower_components",
    "__pycache__", ".venv", "venv", "env", ".env", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "dist", "build", "out", "target", "coverage", ".next", ".nuxt",
    ".turbo", ".cache", ".idea", ".vscode", ".gradle",
}

MAX_FILE_BYTES = 1_000_000   # skip huge (usually generated) files


def list_source_files(repo: str, extensions: set[str]) -> list[str]:
    """Return repo-relative paths of files we have a parser for."""
    paths = _git_files(repo)
    if paths is None:
        paths = _walk_files(repo)

    result = []
    for rel in paths:
        rel = rel.replace(os.sep, "/")
        if not rel.lower().endswith(tuple(extensions)):
            continue
        if _is_skipped(rel):
            continue
        full = os.path.join(repo, rel)
        # git may list files that were deleted but not yet committed.
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_FILE_BYTES:
            continue
        result.append(rel)
    return sorted(set(result))


def file_hash(data: bytes) -> str:
    """Content fingerprint: if this changes, the file must be re-parsed."""
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------

def _is_skipped(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    # Minified / bundled JavaScript is generated code, not something to read.
    return ".min." in name or name.endswith(".bundle.js")


def _git_files(repo: str) -> list[str] | None:
    """Tracked + untracked-but-not-ignored files, or None if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo, capture_output=True, check=True, timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]


def _walk_files(repo: str) -> list[str]:
    found = []
    for root, dirs, files in os.walk(repo):
        # Prune in place so os.walk doesn't descend into skipped folders.
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            found.append(os.path.relpath(os.path.join(root, name), repo))
    return found
