"""RepoSage web app - the API, and the built React frontend.

Run locally:
    cd webapp/backend && uvicorn app.main:app --reload
Then open http://localhost:8000 (after `npm run build` in webapp/frontend),
or run the Vite dev server, which forwards /api here.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .services import ingest, projects
from .services.llm import llm

app = FastAPI(title="RepoSage", docs_url="/api/docs", openapi_url="/api/openapi.json")

SAMPLES = {
    "miniauth": {"title": "Mini login system", "language": "Python",
                 "blurb": "Checks a username and password and gives back a token."},
    "calculator-c": {"title": "Calculator lab", "language": "C",
                     "blurb": "A menu, loops, arrays and functions - a classic first C program."},
    "todo-python": {"title": "To-do list", "language": "Python",
                    "blurb": "Add, finish and list tasks; saves them in a file."},
}


# ---------------------------------------------------------------------------
# Safety: request size and a simple per-visitor rate limit for new projects
# ---------------------------------------------------------------------------

class _RateLimiter:
    """At most `limit` new projects per visitor per hour (kept in memory)."""

    def __init__(self, limit: int, window: float = 3600) -> None:
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, who: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[who]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, "You've analysed a lot of projects this hour. "
                                         "Please take a short break and try again later.")
            q.append(now)


limiter = _RateLimiter(int(os.environ.get("MAX_PROJECTS_PER_HOUR", 30)))


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > config.MAX_UPLOAD_BYTES + 1024 * 1024:
        mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        return JSONResponse({"detail": f"That upload is bigger than {mb} MB. "
                                       "Please upload a smaller project or one folder of it."},
                            status_code=413)
    return await call_next(request)


def _visitor(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _start(request: Request, name: str, source: str, label: str, unpack) -> dict:
    limiter.check(_visitor(request))
    try:
        pid = projects.start(name, source, label, unpack)
    except ingest.UploadError as e:
        raise HTTPException(400, str(e)) from None
    return {"id": pid}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
def get_config() -> dict:
    return {
        "limits": {"upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
                   "files": config.MAX_FILES},
        "languages": ["Python", "JavaScript", "TypeScript", "Java", "C"],
        "samples": [{"id": k, **v} for k, v in SAMPLES.items()],
        "ai": llm.status(),
    }


class PasteIn(BaseModel):
    code: str
    filename: str = ""
    language: str = ""
    name: str = ""


@app.post("/api/projects/paste")
def create_from_paste(body: PasteIn, request: Request) -> dict:
    name = body.name or os.path.splitext(body.filename)[0] or "pasted-code"
    return _start(request, name, "paste", body.filename or "pasted code",
                  lambda dest: ingest.ingest_paste(dest, body.code, body.filename, body.language))


@app.post("/api/projects/upload")
async def create_from_upload(request: Request, files: list[UploadFile] = File(...),
                             paths: list[str] = Form(default=[]), name: str = Form(default="")) -> dict:
    if not files:
        raise HTTPException(400, "Choose at least one file.")
    items, total = [], 0
    for i, f in enumerate(files):
        data = await f.read(config.MAX_UPLOAD_BYTES + 1)
        total += len(data)
        if total > config.MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"That upload is bigger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        # Folder uploads send each file's path inside the folder separately.
        items.append((paths[i] if i < len(paths) and paths[i] else (f.filename or "file"), data))
    first = items[0][0]
    default = first.split("/", 1)[0] if "/" in first else os.path.splitext(first)[0]
    label = first if len(items) == 1 else f"{len(items)} files"
    return _start(request, name or default, "zip" if first.lower().endswith(".zip") else "files",
                  label, lambda dest: ingest.ingest_files(dest, items))


class GithubIn(BaseModel):
    url: str


@app.post("/api/projects/github")
def create_from_github(body: GithubIn, request: Request) -> dict:
    try:
        owner, repo, _ref = ingest.parse_github_url(body.url)
    except ingest.UploadError as e:
        raise HTTPException(400, str(e)) from None
    return _start(request, repo, "github", f"github.com/{owner}/{repo}",
                  lambda dest: ingest.ingest_github(dest, body.url))


class SampleIn(BaseModel):
    sample: str


@app.post("/api/projects/sample")
def create_from_sample(body: SampleIn, request: Request) -> dict:
    if body.sample not in SAMPLES:
        raise HTTPException(404, "No such sample.")
    folder = os.path.join(config.SAMPLES_DIR, body.sample)
    return _start(request, body.sample, "sample", SAMPLES[body.sample]["title"],
                  lambda dest: ingest.ingest_folder(dest, folder))


def _project_or_404(pid: str) -> dict:
    project = db.get_project(pid) if projects.valid_id(pid) else None
    if project is None:
        raise HTTPException(404, "We couldn't find that project. Projects are deleted after a while; "
                                 "please upload it again.")
    return project


@app.get("/api/projects/{pid}")
def project_status(pid: str) -> dict:
    p = _project_or_404(pid)
    return {k: p[k] for k in ("id", "name", "source", "source_label", "status", "stage",
                              "progress", "error", "ai_status", "stats")}


@app.get("/api/projects/{pid}/overview")
def project_overview(pid: str) -> dict:
    p = _project_or_404(pid)
    if p["status"] != "ready":
        raise HTTPException(409, "Still reading the code, please wait.")
    data = projects.overview(pid)
    if data is None:
        raise HTTPException(404, "The analysis of this project is missing. Please upload it again.")
    data["ai_status"] = p["ai_status"]
    data["ai"] = {"providers": llm.status()["providers"]}
    return data


@app.post("/api/projects/{pid}/ai/retry")
def project_ai_retry(pid: str) -> dict:
    p = _project_or_404(pid)
    if p["status"] == "ready" and p["ai_status"] in ("busy", "resting", "off"):
        projects.retry_notes(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/source")
def project_source(pid: str, path: str) -> dict:
    _project_or_404(pid)
    text = projects.read_source(pid, path)
    if text is None:
        raise HTTPException(404, "No such file in this project.")
    return {"path": path, "text": text}


# ---------------------------------------------------------------------------
# The React app (built into webapp/frontend/dist)
# ---------------------------------------------------------------------------

if os.path.isdir(os.path.join(config.FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(config.FRONTEND_DIST, "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    dist = os.path.realpath(config.FRONTEND_DIST)
    candidate = os.path.realpath(os.path.join(dist, full_path))
    if full_path and candidate.startswith(dist + os.sep) and os.path.isfile(candidate):
        return FileResponse(candidate)                     # fonts, icons, ...
    index = os.path.join(dist, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built yet: run `npm run build` in webapp/frontend."},
                        status_code=503)
