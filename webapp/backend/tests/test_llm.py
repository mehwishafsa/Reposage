"""The AI layer against fake HTTP servers: error reasons, Gemini endpoints,
retired models, and keys never reaching the logs."""

import json
import logging
import os
import tempfile
import unittest

os.environ.setdefault("REPOSAGE_DATA", tempfile.mkdtemp(prefix="reposage-web-test-"))

import httpx  # noqa: E402

from app import config  # noqa: E402
from app.services import glossary  # noqa: E402
from app.services.llm import (AIUnavailable, GeminiProvider, ProviderError, RateLimited, classify,  # noqa: E402
                              llm, pick_model)

AQ_KEY = "AQ.TestKeyNotReal_123456789"
AIZA_KEY = "AIzaTestKeyNotReal_1234567890123456789"


def google_error(code: int, status: str, message: str, reason: str = "") -> str:
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}] if reason else []
    return json.dumps({"error": {"code": code, "status": status, "message": message, "details": details}})


OK = {"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}]}


class ClassifyTest(unittest.TestCase):
    def test_reasons(self):
        self.assertEqual(classify(400, google_error(400, "INVALID_ARGUMENT", "API key not valid.", "API_KEY_INVALID")).kind, "auth")
        self.assertEqual(classify(401, google_error(401, "UNAUTHENTICATED", "Expected OAuth 2 access token",
                                                    "ACCESS_TOKEN_TYPE_UNSUPPORTED")).kind, "auth")
        self.assertEqual(classify(404, google_error(404, "NOT_FOUND", "models/gemini-9 is not found")).kind, "model")
        self.assertEqual(classify(400, google_error(400, "FAILED_PRECONDITION", "User location is not supported")).kind, "region")
        e = classify(429, google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel"))
        self.assertIsInstance(e, RateLimited)
        self.assertTrue(e.daily)
        e = classify(429, google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded per minute"), retry_after=7)
        self.assertEqual((e.daily, e.retry_after), (False, 7))
        self.assertIsInstance(classify(503, google_error(503, "UNAVAILABLE", "The model is overloaded")), RateLimited)
        self.assertEqual(classify(400, google_error(400, "INVALID_ARGUMENT", "Unknown field")).kind, "bad_request")

    def test_pick_model(self):
        names = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash-preview",
                 "gemini-2.5-flash-preview-tts", "gemini-2.5-pro", "gemini-2.5-flash-image"]
        self.assertEqual(pick_model(names, "smart"), "gemini-2.5-flash")       # stable beats preview
        self.assertEqual(pick_model(names, "fast"), "gemini-2.5-flash-lite")
        self.assertIsNone(pick_model(["gemini-2.5-pro"], "fast"))


class GeminiEndpointTest(unittest.TestCase):
    def provider(self, key: str, handler) -> GeminiProvider:
        os.environ["GEMINI_API_KEY"] = key
        return GeminiProvider(httpx.Client(transport=httpx.MockTransport(handler)))

    def tearDown(self):
        os.environ.pop("GEMINI_API_KEY", None)

    def test_aq_key_goes_to_vertex_with_query_key(self):
        seen = []

        def handler(req):
            seen.append((req.url.host, req.url.params.get("key"), req.headers.get("x-goog-api-key")))
            return httpx.Response(200, json=OK)
        p = self.provider(AQ_KEY, handler)
        self.assertEqual(p.send("s", "p", "gemini-2.5-flash", 50, False), "hello")
        self.assertEqual(seen, [("aiplatform.googleapis.com", AQ_KEY, None)])

    def test_aiza_key_goes_to_ai_studio_in_a_header(self):
        seen = []

        def handler(req):
            seen.append((req.url.host, req.url.params.get("key"), req.headers.get("x-goog-api-key")))
            return httpx.Response(200, json=OK)
        p = self.provider(AIZA_KEY, handler)
        p.send("s", "p", "gemini-2.5-flash", 50, False)
        self.assertEqual(seen, [("generativelanguage.googleapis.com", None, AIZA_KEY)])

    def test_falls_back_to_the_other_endpoint_and_remembers(self):
        hosts = []

        def handler(req):
            hosts.append(req.url.host)
            if req.url.host == "aiplatform.googleapis.com":
                return httpx.Response(401, text=google_error(401, "UNAUTHENTICATED", "API key not valid",
                                                             "ACCESS_TOKEN_TYPE_UNSUPPORTED"))
            return httpx.Response(200, json=OK)
        p = self.provider(AQ_KEY, handler)
        p.send("s", "p", "m", 50, False)
        p.send("s", "p", "m", 50, False)
        self.assertEqual(hosts, ["aiplatform.googleapis.com", "generativelanguage.googleapis.com",
                                 "generativelanguage.googleapis.com"])

    def test_both_endpoints_refuse_the_key(self):
        p = self.provider(AQ_KEY, lambda req: httpx.Response(
            401, text=google_error(401, "UNAUTHENTICATED", "bad credentials", "ACCESS_TOKEN_TYPE_UNSUPPORTED")))
        with self.assertRaises(ProviderError) as ctx:
            p.send("s", "p", "m", 50, False)
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertIn("vertex:", ctx.exception.detail)
        self.assertIn("aistudio:", ctx.exception.detail)

    def test_retired_model_is_replaced(self):
        calls = []

        def handler(req):
            calls.append(req.url.path)
            if req.method == "GET":
                return httpx.Response(200, json={"models": [
                    {"name": "models/gemini-3.1-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]})
            if "gemini-2.5-flash:" in req.url.path:
                return httpx.Response(404, text=google_error(404, "NOT_FOUND", "models/gemini-2.5-flash is not found"))
            return httpx.Response(200, json=OK)
        p = self.provider(AIZA_KEY, handler)
        with self.assertLogs("reposage.ai", "WARNING") as logs:
            self.assertEqual(p.send("s", "p", "gemini-2.5-flash", 50, False), "hello")
        self.assertEqual(p.model("smart"), "gemini-3.1-flash")
        self.assertIn("using 'gemini-3.1-flash' instead", "\n".join(logs.output))

    def test_empty_answer_is_bad_output(self):
        p = self.provider(AIZA_KEY, lambda req: httpx.Response(200, json={"candidates": [
            {"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}))
        with self.assertRaises(ProviderError) as ctx:
            p.send("s", "p", "m", 50, False)
        self.assertEqual(ctx.exception.kind, "bad_output")
        self.assertIn("MAX_TOKENS", ctx.exception.detail)


class StudentMessagesAndLogsTest(unittest.TestCase):
    def setUp(self):
        self.old = (config.AI_PROVIDER, config.AI_FALLBACK)
        config.AI_PROVIDER, config.AI_FALLBACK = "gemini", "none"
        os.environ["GEMINI_API_KEY"] = AQ_KEY
        llm.reset()
        llm.sleep = lambda s: None

    def tearDown(self):
        config.AI_PROVIDER, config.AI_FALLBACK = self.old
        os.environ.pop("GEMINI_API_KEY", None)
        llm.reset()

    def use(self, handler):
        p = llm.chain()[0]
        p.client = httpx.Client(transport=httpx.MockTransport(handler))

    def reason(self, prompt: str) -> AIUnavailable:
        with self.assertRaises(AIUnavailable) as ctx:
            llm.ask("t", "s", prompt)
        return ctx.exception

    def test_bad_key_says_setup_and_logs_why_without_the_key(self):
        self.use(lambda req: httpx.Response(401, text=google_error(
            401, "UNAUTHENTICATED", f"bad key {AQ_KEY}", "ACCESS_TOKEN_TYPE_UNSUPPORTED")))
        with self.assertLogs("reposage.ai", "INFO") as logs:
            e = self.reason("p1")
        self.assertEqual(e.reason, "setup")
        self.assertIn("isn't set up correctly", str(e))
        text = "\n".join(logs.output)
        self.assertIn("reason=auth", text)
        self.assertNotIn(AQ_KEY, text)
        self.assertIn("***", text)
        # the broken provider isn't asked again for a while
        self.use(lambda req: (_ for _ in ()).throw(AssertionError("should not be called")))
        self.assertEqual(self.reason("p2").reason, "setup")

    def test_rate_limit_says_busy(self):
        self.use(lambda req: httpx.Response(429, text=google_error(429, "RESOURCE_EXHAUSTED", "per minute")))
        e = self.reason("p3")
        self.assertEqual(e.reason, "busy")
        self.assertIn("busy", str(e))

    def test_daily_quota_says_resting(self):
        self.use(lambda req: httpx.Response(429, text=google_error(
            429, "RESOURCE_EXHAUSTED", "Quota exceeded for GenerateRequestsPerDayPerProjectPerModel")))
        self.assertEqual(self.reason("p4").reason, "daily_limit")

    def test_check_endpoint_reports_reason(self):
        from fastapi.testclient import TestClient
        from app import main
        self.use(lambda req: httpx.Response(404, text=google_error(404, "NOT_FOUND", "model not found")))
        main._last_check[0] = 0
        body = TestClient(main.app).get("/api/ai/check").json()
        self.assertEqual((body["ok"], body["reason"]), (False, "model"))
        self.assertIn("Vertex AI express style", body["key"]["gemini"])
        self.assertNotIn(AQ_KEY, json.dumps(body))


class GlossaryTest(unittest.TestCase):
    def test_terms_and_concepts(self):
        self.assertEqual([t.key for t in glossary.find_terms("what is for loop used for?")], ["for_loop"])
        self.assertTrue(glossary.is_concept_question("what is for loop used for?"))
        self.assertTrue(glossary.is_concept_question("Why use a switch statement?"))
        self.assertFalse(glossary.is_concept_question("What does the loop in main() do?"))
        self.assertFalse(glossary.is_concept_question("What happens if I divide by zero?"))
        self.assertEqual(glossary.find_terms("save the file while the program runs"), [])   # English, not a loop
        self.assertEqual(glossary.find_terms("explain the while loop")[0].key, "while_loop")


if __name__ == "__main__":
    unittest.main()
