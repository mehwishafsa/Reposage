"""Bring a student's code into a project folder - SAFELY.

Ways in: pasted code, picked files, a .zip, or a public GitHub URL.

Safety rules (the code is only ever READ and parsed, never run):
  - size limits: upload size, unpacked size (zip bombs), number of files
  - no path tricks: absolute paths, "..", drive letters and links are refused,
    so nothing can be written outside the project folder
  - skipped: node_modules / build folders, binaries, and secret files
    (.env, private keys, credential files) - we don't even store them
  - only files RepoSage can read are kept (source code + README)
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass, field

import httpx

from reposage.parsers import supported_extensions
from reposage.scanner import SKIP_DIRS

from .. import config


class UploadError(Exception):
    """A problem we can explain to the student in plain words."""


SECRET_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "service-account.json", ".htpasswd", ".git-credentials",
}
SECRET_EXTS = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".crt", ".der"}
README_RE = re.compile(r"^readme(\.(md|txt|rst))?$", re.I)


@dataclass
class IngestReport:
    kept: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)   # reason -> count
    total_bytes: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


# ---------------------------------------------------------------------------
# Rules for a single path
# ---------------------------------------------------------------------------

def safe_relpath(name: str) -> str | None:
    """A clean "folder/file.py" path, or None if the name is unsafe."""
    name = name.replace("\\", "/")
    if not name or "\x00" in name or any(ord(ch) < 32 for ch in name):
        return None
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None                                     # absolute path / drive letter
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None                                     # escapes the folder
    return posixpath.join(*parts)


def skip_reason(rel: str) -> str | None:
    """Why this file is not kept (None = keep it)."""
    parts = rel.split("/")
    filename = parts[-1]
    lower = filename.lower()
    if any(p in SKIP_DIRS or (p.startswith(".") and p != ".") for p in parts[:-1]):
        return "library or hidden folders"
    if lower.startswith(".env") or lower in SECRET_NAMES or posixpath.splitext(lower)[1] in SECRET_EXTS:
        return "secret files (.env, keys)"
    if README_RE.match(filename):
        return None
    if posixpath.splitext(lower)[1] not in supported_extensions():
        return "not code RepoSage reads"
    if ".min." in lower or lower.endswith(".bundle.js"):
        return "generated code"
    return None


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


class _Writer:
    """Writes accepted files into the project's src/ folder, enforcing limits."""

    def __init__(self, dest: str) -> None:
        self.dest = os.path.realpath(dest)
        os.makedirs(self.dest, exist_ok=True)
        self.report = IngestReport()

    def add(self, name: str, data: bytes) -> None:
        rel = safe_relpath(name)
        if rel is None:
            self.report.skip("unsafe file names")
            return
        reason = skip_reason(rel)
        if reason:
            self.report.skip(reason)
            return
        if len(data) > config.MAX_FILE_BYTES:
            self.report.skip("files over 1 MB")
            return
        if _looks_binary(data):
            self.report.skip("binary files")
            return
        if len(self.report.kept) >= config.MAX_FILES:
            raise UploadError(f"That project has more than {config.MAX_FILES} code files. "
                              "Please upload one part of it (for example one folder).")
        full = os.path.realpath(os.path.join(self.dest, rel))
        if not full.startswith(self.dest + os.sep):     # belt and braces
            self.report.skip("unsafe file names")
            return
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        self.report.kept.append(rel)
        self.report.total_bytes += len(data)

    def done(self) -> IngestReport:
        if not self.report.kept:
            raise UploadError("We couldn't find any code we can read. RepoSage understands "
                              "Python, JavaScript, TypeScript, Java and C files.")
        self.report.kept.sort()
        return self.report


# ---------------------------------------------------------------------------
# The four ways in
# ---------------------------------------------------------------------------

LANG_EXT = {"python": ".py", "javascript": ".js", "typescript": ".ts", "java": ".java", "c": ".c"}


def ingest_paste(dest: str, code: str, filename: str = "", language: str = "") -> IngestReport:
    if not code.strip():
        raise UploadError("The code box is empty. Paste some code first.")
    if len(code) > config.MAX_PASTE_CHARS:
        raise UploadError("That's a lot of code for the paste box. Please upload it as a .zip instead.")
    name = os.path.basename(filename.strip().replace("\\", "/")) if filename.strip() else ""
    if not name:
        name = "main" + LANG_EXT.get(language, guess_extension(code))
    elif "." not in name:
        name += LANG_EXT.get(language, guess_extension(code))
    w = _Writer(dest)
    w.add(name, code.encode("utf-8"))
    return w.done()


def guess_extension(code: str) -> str:
    """Rough guess of the language of pasted code (the student can pick it too)."""
    if re.search(r"#include\s*[<\"]", code) or re.search(r"\bint\s+main\s*\(", code) and "public" not in code:
        return ".c"
    if re.search(r"\b(public|private)\s+(static\s+)?(class|void)\b", code):
        return ".java"
    if re.search(r"^\s*(def |import |from \w+ import|class \w+.*:)", code, re.M):
        return ".py"
    if re.search(r":\s*(string|number|boolean)\b|\binterface\s+\w+\s*{", code):
        return ".ts"
    return ".js" if re.search(r"\b(function|const|let|=>)\b", code) else ".py"


def ingest_files(dest: str, files: list[tuple[str, bytes]]) -> IngestReport:
    total = sum(len(d) for _, d in files)
    if total > config.MAX_UPLOAD_BYTES:
        raise UploadError(_too_big())
    if len(files) == 1 and files[0][0].lower().endswith(".zip"):
        return ingest_zip(dest, files[0][1])
    # A picked folder sends "myproject/src/a.py"; drop the "myproject/" part.
    prefix = _common_top_folder([name for name, _ in files])
    w = _Writer(dest)
    for name, data in files:
        w.add(name[len(prefix):] if prefix else name, data)
    return w.done()


def ingest_zip(dest: str, data: bytes) -> IngestReport:
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(_too_big())
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError("That file isn't a valid .zip. Try zipping the folder again.") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    prefix = _common_top_folder([i.filename for i in infos])
    w = _Writer(dest)
    unpacked = 0
    for info in infos:
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            w.report.skip("links")                       # could point anywhere on the server
            continue
        if info.flag_bits & 0x1:
            w.report.skip("password-protected files")
            continue
        name = info.filename[len(prefix):] if prefix else info.filename
        rel = safe_relpath(name)
        if rel is None:
            w.report.skip("unsafe file names")
            continue
        if skip_reason(rel):
            w.report.skip(skip_reason(rel))
            continue
        if info.file_size > config.MAX_FILE_BYTES:
            w.report.skip("files over 1 MB")
            continue
        # Read at most limit+1 bytes: the size in the zip header could be a lie.
        with zf.open(info) as fh:
            content = fh.read(config.MAX_FILE_BYTES + 1)
        unpacked += len(content)
        if unpacked > config.MAX_UNPACKED_BYTES:
            raise UploadError("That .zip unpacks to too much data. Please upload a smaller part.")
        w.add(rel, content)
    return w.done()


GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?"
    r"(?:/tree/([A-Za-z0-9._/-]{1,200}))?/?$")


def parse_github_url(url: str) -> tuple[str, str, str | None]:
    m = GITHUB_RE.match(url.strip())
    if not m:
        raise UploadError("That doesn't look like a GitHub repository link. "
                          "It should look like https://github.com/owner/project")
    return m.group(1), m.group(2), m.group(3)


def ingest_github(dest: str, url: str, client: httpx.Client | None = None) -> IngestReport:
    owner, repo, ref = parse_github_url(url)
    archive = f"https://github.com/{owner}/{repo}/archive/{ref or 'HEAD'}.zip"
    data = download_limited(archive, client)
    return ingest_zip(dest, data)


def download_limited(url: str, client: httpx.Client | None = None) -> bytes:
    """Download a public GitHub archive, stopping as soon as it's too big."""
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=60)
    try:
        with client.stream("GET", url) as resp:
            host = resp.url.host
            if host not in ("github.com", "codeload.github.com"):
                raise UploadError("GitHub sent us somewhere unexpected, so we stopped.")
            if resp.status_code == 404:
                raise UploadError("We couldn't find that repository. Is it public, and is the link right?")
            if resp.status_code != 200:
                raise UploadError(f"GitHub didn't send the code (error {resp.status_code}). Try again later.")
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise UploadError(_too_big())
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError:
        raise UploadError("We couldn't reach GitHub. Check the link or try again in a minute.") from None
    finally:
        if own:
            client.close()


def ingest_folder(dest: str, folder: str) -> IngestReport:
    """Copy one of our built-in sample projects (trusted, but same rules)."""
    w = _Writer(dest)
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                w.add(os.path.relpath(full, folder).replace(os.sep, "/"), fh.read())
    return w.done()


def _common_top_folder(names: list[str]) -> str:
    """GitHub zips put everything in "repo-main/"; strip that one folder."""
    tops = {n.replace("\\", "/").split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n.replace("\\", "/") for n in names):
        return tops.pop() + "/"
    return ""


def _too_big() -> str:
    return (f"That upload is bigger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "Please upload a smaller project or one folder of it.")
