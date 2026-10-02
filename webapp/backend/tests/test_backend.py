"""Backend tests. They use the FAKE AI provider and never touch the network.

Run:  cd webapp/backend && .venv/bin/python -m unittest discover -s tests -t .
"""

import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
import zipfile

_DATA = tempfile.mkdtemp(prefix="reposage-web-test-")
os.environ.update({"REPOSAGE_DATA": _DATA, "AI_PROVIDER": "fake", "AI_FALLBACK": "none"})

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db  # noqa: E402
from app.main import app, limiter  # noqa: E402
from app.services import ingest  # noqa: E402
from app.services.llm import AIUnavailable, FakeProvider, RateLimited, llm  # noqa: E402

client = TestClient(app)


def make_zip(entries: dict, symlinks: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return buf.getvalue()


def wait_ready(pid: str) -> dict:
    for _ in range(200):
        st = client.get(f"/api/projects/{pid}").json()
        if st["status"] != "working" and st["ai_status"] not in ("waiting", "thinking"):
            return st
        time.sleep(0.05)
    raise AssertionError("project never finished")


class IngestSafetyTest(unittest.TestCase):
    def setUp(self):
        self.dest = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_zip_path_tricks_are_refused(self):
        data = make_zip({"../evil.py": "x=1", "/abs.py": "x=1", "ok/../../up.py": "x=1",
                         "C:/win.py": "x=1", "good.py": "print(1)"},
                        symlinks={"link.py": "/etc/passwd"})
        report = ingest.ingest_zip(self.dest, data)
        self.assertEqual(report.kept, ["good.py"])
        self.assertEqual(report.skipped.get("unsafe file names"), 4)
        self.assertEqual(report.skipped.get("links"), 1)
        for root, _dirs, files in os.walk(os.path.dirname(self.dest)):
            self.assertNotIn("evil.py", files)

    def test_secrets_libraries_and_binaries_are_skipped(self):
        data = make_zip({"proj/app.py": "import os", "proj/.env": "KEY=secret",
                         "proj/.env.local": "KEY=1", "proj/id_rsa": "-----", "proj/server.pem": "-",
                         "proj/node_modules/lib/index.js": "x", "proj/logo.png": "\x89PNG",
                         "proj/weird.py": "a\x00b", "proj/README.md": "# Hi"})
        report = ingest.ingest_zip(self.dest, data)
        self.assertEqual(report.kept, ["README.md", "app.py"])      # top folder "proj/" stripped
        self.assertEqual(report.skipped["secret files (.env, keys)"], 4)
        self.assertEqual(report.skipped["library or hidden folders"], 1)
        self.assertEqual(report.skipped["binary files"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.dest, ".env")))

    def test_limits(self):
        old = config.MAX_FILES
        config.MAX_FILES = 3
        try:
            with self.assertRaises(ingest.UploadError):
                ingest.ingest_zip(self.dest, make_zip({f"f{i}.py": "x=1" for i in range(5)}))
        finally:
            config.MAX_FILES = old
        old = config.MAX_UNPACKED_BYTES
        config.MAX_UNPACKED_BYTES = 1000
        try:
            with self.assertRaises(ingest.UploadError):     # zip bomb style: unpacks too big
                ingest.ingest_zip(tempfile.mkdtemp(), make_zip({f"f{i}.py": "x" * 900 for i in range(3)}))
        finally:
            config.MAX_UNPACKED_BYTES = old
        with self.assertRaises(ingest.UploadError):
            ingest.ingest_zip(self.dest, b"not a zip")

    def test_paste_guesses_language(self):
        r = ingest.ingest_paste(self.dest, '#include <stdio.h>\nint main(void){return 0;}')
        self.assertEqual(r.kept, ["main.c"])
        r = ingest.ingest_paste(tempfile.mkdtemp(), "def f():\n    return 1\n", "../../hack")
        self.assertEqual(r.kept, ["hack.py"])
        with self.assertRaises(ingest.UploadError):
            ingest.ingest_paste(self.dest, "   ")

    def test_github_url_rules(self):
        self.assertEqual(ingest.parse_github_url("https://github.com/sindresorhus/ky"), ("sindresorhus", "ky", None))
        self.assertEqual(ingest.parse_github_url("github.com/a/b.git"), ("a", "b", None))
        self.assertEqual(ingest.parse_github_url("https://github.com/a/b/tree/dev"), ("a", "b", "dev"))
        for bad in ("https://gitlab.com/a/b", "https://github.com/a", "http://evil.com/github.com/a/b",
                    "https://github.com/a/b/../../x", "file:///etc/passwd"):
            with self.assertRaises(ingest.UploadError, msg=bad):
                ingest.parse_github_url(bad)


class GithubDownloadTest(unittest.TestCase):
    """GitHub is mocked: the real download is only possible on the server."""

    def client(self, handler):
        import httpx
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    def test_download_follows_github_redirect_and_unpacks(self):
        import httpx
        archive = make_zip({"ky-main/source/index.ts": "export function ky() {}", "ky-main/.env": "X=1"})

        def handler(req):
            if req.url.host == "github.com":
                return httpx.Response(302, headers={"location": "https://codeload.github.com/o/r/zip/HEAD"})
            return httpx.Response(200, content=archive)
        report = ingest.ingest_github(tempfile.mkdtemp(), "https://github.com/o/r", self.client(handler))
        self.assertEqual(report.kept, ["source/index.ts"])

    def test_refuses_other_hosts_too_big_and_missing(self):
        import httpx
        evil = lambda req: (httpx.Response(302, headers={"location": "https://evil.example/x.zip"})
                            if req.url.host == "github.com" else httpx.Response(200, content=b"x"))
        with self.assertRaises(ingest.UploadError):
            ingest.ingest_github(tempfile.mkdtemp(), "https://github.com/o/r", self.client(evil))
        old = config.MAX_UPLOAD_BYTES
        config.MAX_UPLOAD_BYTES = 100
        try:
            with self.assertRaises(ingest.UploadError):
                ingest.ingest_github(tempfile.mkdtemp(), "https://github.com/o/r",
                                     self.client(lambda req: httpx.Response(200, content=b"x" * 500)))
        finally:
            config.MAX_UPLOAD_BYTES = old
        with self.assertRaisesRegex(ingest.UploadError, "Is it public"):
            ingest.ingest_github(tempfile.mkdtemp(), "https://github.com/o/private",
                                 self.client(lambda req: httpx.Response(404)))


class LLMTest(unittest.TestCase):
    def setUp(self):
        llm.reset()
        llm.sleep = lambda s: None
        FakeProvider.fail_next.clear()
        FakeProvider.calls.clear()

    def test_cache_means_one_request(self):
        a = llm.ask("demo", "sys", "same prompt for cache test")
        b = llm.ask("demo", "sys", "same prompt for cache test")
        self.assertEqual(a, b)
        self.assertEqual(FakeProvider.calls, ["demo"])

    def test_retries_politely_on_rate_limit(self):
        FakeProvider.fail_next.extend([RateLimited(1), RateLimited(None)])
        waits = []
        llm.sleep = waits.append
        self.assertTrue(llm.ask("demo", "sys", "retry prompt"))
        self.assertEqual(waits[0], 1)                 # waited as long as the provider asked

    def test_daily_limit_gives_friendly_reason(self):
        FakeProvider.fail_next.append(RateLimited(daily=True))
        with self.assertRaises(AIUnavailable) as ctx:
            llm.ask("demo", "sys", "daily prompt")
        self.assertEqual(ctx.exception.reason, "daily_limit")
        self.assertIn("today's limit", str(ctx.exception))
        self.assertTrue(llm.status()["resting"])

    def test_bad_json_is_never_cached(self):
        FakeProvider.handlers["badjson"] = lambda s, p: "sorry, no json here"
        with self.assertRaises(AIUnavailable):
            llm.ask("badjson", "sys", "p", json=True)
        FakeProvider.handlers["badjson"] = lambda s, p: 'Sure! ```json\n{"a": 1}\n```'
        self.assertEqual(json.loads(llm.ask("badjson", "sys", "p", json=True)), {"a": 1})

    def test_fallback_provider(self):
        old = (config.AI_PROVIDER, config.AI_FALLBACK)
        config.AI_PROVIDER, config.AI_FALLBACK = "gemini", "fake"   # gemini has no key here
        try:
            llm.reset()
            self.assertEqual([p.name for p in llm.chain()], ["fake"])
            config.AI_FALLBACK = "groq"
            llm.reset()
            with self.assertRaises(AIUnavailable) as ctx:
                llm.ask("demo", "sys", "no provider prompt")
            self.assertEqual(ctx.exception.reason, "off")
        finally:
            config.AI_PROVIDER, config.AI_FALLBACK = old
            llm.reset()


class ApiTest(unittest.TestCase):
    def setUp(self):
        llm.reset()
        llm.sleep = lambda s: None
        FakeProvider.fail_next.clear()
        limiter.hits.clear()

    def test_sample_c_project_end_to_end(self):
        pid = client.post("/api/projects/sample", json={"sample": "calculator-c"}).json()["id"]
        st = wait_ready(pid)
        self.assertEqual((st["status"], st["ai_status"]), ("ready", "done"))
        ov = client.get(f"/api/projects/{pid}/overview").json()
        self.assertEqual(ov["stats"]["files"], 5)
        self.assertEqual(ov["stats"]["functions"], 13)
        self.assertEqual(ov["start_here"]["file"], "main.c")
        self.assertEqual(ov["start_here"]["function"], {"name": "main", "line": 42})
        self.assertEqual([s["file"] for s in ov["start_here"]["steps"]], ["main.c", "calc.c", "stats.c"])
        self.assertEqual(ov["languages"][0]["name"], "C")
        self.assertIn({"source": "main.c", "target": "calc.c", "imports": 0, "calls": 5}, ov["map"]["links"])
        self.assertEqual(ov["overview"]["source"], "ai")
        self.assertTrue(all(f["summary_source"] == "ai" for f in ov["files"]))

    def test_works_without_ai(self):
        old = config.AI_PROVIDER
        config.AI_PROVIDER = "gemini"                 # no key -> AI off
        llm.reset()
        try:
            pid = client.post("/api/projects/sample", json={"sample": "miniauth"}).json()["id"]
            st = wait_ready(pid)
            self.assertEqual((st["status"], st["ai_status"]), ("ready", "off"))
            ov = client.get(f"/api/projects/{pid}/overview").json()
            self.assertEqual(ov["overview"]["source"], "code")
            self.assertIn("starts in main.py, in route()", ov["overview"]["text"])
            auth = next(f for f in ov["files"] if f["path"] == "auth.py")
            self.assertEqual(auth["summary"], "Authentication logic: verifying credentials and issuing tokens.")
        finally:
            config.AI_PROVIDER = old
            llm.reset()

    def test_paste_upload_and_errors(self):
        pid = client.post("/api/projects/paste", json={"code": "def hello():\n    return 1\n"}).json()["id"]
        self.assertEqual(wait_ready(pid)["status"], "ready")
        files = [("files", ("a.py", b"import b\nb.f()\n")), ("files", ("b.py", b"def f():\n    pass\n"))]
        resp = client.post("/api/projects/upload", files=files, data={"paths": ["proj/a.py", "proj/b.py"]})
        pid = resp.json()["id"]
        self.assertEqual(wait_ready(pid)["stats"]["files"], 2)
        src = client.get(f"/api/projects/{pid}/source", params={"path": "a.py"}).json()
        self.assertIn("import b", src["text"])
        self.assertEqual(client.get(f"/api/projects/{pid}/source", params={"path": "../../reposage.db"}).status_code, 404)
        bad = client.post("/api/projects/upload", files=[("files", ("x.zip", b"nope"))])
        self.assertEqual(bad.status_code, 400)
        self.assertIn("valid .zip", bad.json()["detail"])
        self.assertEqual(client.get("/api/projects/doesnotexist").status_code, 404)
        self.assertEqual(client.post("/api/projects/github", json={"url": "https://evil.com/a/b"}).status_code, 400)

    def test_rate_limit_per_visitor(self):
        old = limiter.limit
        limiter.limit = 2
        try:
            codes = [client.post("/api/projects/paste", json={"code": "x = 1"}).status_code for _ in range(3)]
            self.assertEqual(codes, [200, 200, 429])
        finally:
            limiter.limit = old

    def test_config_has_no_secrets(self):
        os.environ["GEMINI_API_KEY"] = "super-secret-test-key"
        try:
            body = client.get("/api/config").text
            self.assertNotIn("super-secret-test-key", body)
        finally:
            del os.environ["GEMINI_API_KEY"]


if __name__ == "__main__":
    unittest.main()
