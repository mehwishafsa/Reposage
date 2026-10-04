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

    def test_diagnostics_page_needs_the_token_and_reports_errors(self):
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        os.environ.pop("DIAG_TOKEN", None)
        self.assertEqual(client.get("/api/health/ai?token=x").status_code, 404)     # off without DIAG_TOKEN
        os.environ["DIAG_TOKEN"] = "letmein"
        try:
            self.assertEqual(client.get("/api/health/ai?token=wrong").status_code, 403)
            self.assertEqual(client.get("/api/ai/check").status_code, 404)
            gemini = llm.chain()[0]

            def google(req):
                if req.method == "GET":
                    return httpx.Response(200, json={"models": [
                        {"name": "models/gemini-3.1-flash", "supportedGenerationMethods": ["generateContent"]}]})
                if "lite" in req.url.path:
                    return httpx.Response(404, text=google_error(404, "NOT_FOUND", "models/x is not found"))
                body = json.loads(req.content)
                if "tools" in body:
                    return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                        {"functionCall": {"name": "ping", "args": {"word": "hello"}}}]}}]})
                return httpx.Response(200, json=OK)
            original = main.llm._providers
            import app.services.llm as llm_module
            real_provider = llm_module.PROVIDERS["gemini"]
            llm_module.PROVIDERS["gemini"] = lambda: gemini
            gemini.client = httpx.Client(transport=httpx.MockTransport(google))
            main._last_check[0] = 0
            try:
                report = client.get("/api/health/ai", params={"token": "letmein"}).text
            finally:
                llm_module.PROVIDERS["gemini"] = real_provider
            self.assertIn("Vertex AI express style (AQ....), 27 characters", report)
            self.assertIn("fast model gemini-2.5-flash-lite via vertex: FAILED HTTP 404 reason=model", report)
            self.assertIn("smart model gemini-2.5-flash via vertex: OK", report)
            self.assertIn("function calling gemini-2.5-flash via vertex: OK", report)
            self.assertIn("[tool call: ping({'word': 'hello'})]", report)
            self.assertNotIn(AQ_KEY, report)
            self.assertEqual(client.get("/api/health/ai", params={"token": "letmein"}).status_code, 429)
        finally:
            os.environ.pop("DIAG_TOKEN", None)


class ToolCallingFormatTest(unittest.TestCase):
    """What each provider receives and how its reply is read."""

    TOOLS = [{"name": "read_function", "description": "Read code.", "parameters": {
        "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}]

    def conversation(self):
        return [{"role": "user", "text": "How does divide work?"},
                {"role": "assistant", "text": "", "calls": [{"id": "c1", "name": "read_function", "args": {"name": "divide"}}],
                 "raw": {"gemini_parts": [{"functionCall": {"name": "read_function", "args": {"name": "divide"}},
                                           "thoughtSignature": "sig123"}]}},
                {"role": "tool", "results": [{"id": "c1", "name": "read_function", "content": "  25 | return a / b;"}]}]

    def test_gemini(self):
        sent = []

        def handler(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "final_answer", "args": {"answer": "It divides [calc.c:25]."}},
                 "thoughtSignature": "sig456"}]}}]})
        os.environ["GEMINI_API_KEY"] = AIZA_KEY
        try:
            p = GeminiProvider(httpx.Client(transport=httpx.MockTransport(handler)))
            reply = p.send_tools("sys", self.conversation(), self.TOOLS, "gemini-2.5-flash", 100, force="final_answer")
        finally:
            os.environ.pop("GEMINI_API_KEY", None)
        body = sent[0]
        self.assertEqual(body["tools"][0]["functionDeclarations"][0]["name"], "read_function")
        self.assertEqual(body["toolConfig"]["functionCallingConfig"], {"mode": "ANY", "allowedFunctionNames": ["final_answer"]})
        self.assertEqual(body["contents"][1]["parts"][0]["thoughtSignature"], "sig123")   # sent back unchanged
        self.assertEqual(body["contents"][2]["parts"][0]["functionResponse"],
                         {"name": "read_function", "response": {"content": "  25 | return a / b;"}})
        self.assertEqual(reply.calls[0]["name"], "final_answer")
        self.assertEqual(reply.raw["gemini_parts"][0]["thoughtSignature"], "sig456")

    def test_groq(self):
        from app.services.llm import GroqProvider
        sent = []

        def handler(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "t9", "type": "function", "function": {"name": "find_callers", "arguments": '{"name": "divide"}'}}]}}]})
        p = GroqProvider(httpx.Client(transport=httpx.MockTransport(handler)))
        reply = p.send_tools("sys", self.conversation(), self.TOOLS, "llama", 100)
        msgs = sent[0]["messages"]
        self.assertEqual(msgs[2]["tool_calls"][0]["function"], {"name": "read_function", "arguments": '{"name": "divide"}'})
        self.assertEqual(msgs[3], {"role": "tool", "tool_call_id": "c1", "content": "  25 | return a / b;"})
        self.assertEqual(sent[0]["tool_choice"], "auto")
        self.assertEqual(reply.calls, [{"id": "t9", "name": "find_callers", "args": {"name": "divide"}}])

    def test_anthropic(self):
        from app.services.llm import AnthropicProvider
        sent = []

        def handler(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Let me check."},
                                                         {"type": "tool_use", "id": "tu1", "name": "find_callers",
                                                          "input": {"name": "divide"}}]})
        p = AnthropicProvider(httpx.Client(transport=httpx.MockTransport(handler)))
        reply = p.send_tools("sys", self.conversation(), self.TOOLS, "claude", 100)
        msgs = sent[0]["messages"]
        self.assertEqual(msgs[1]["content"][0], {"type": "tool_use", "id": "c1", "name": "read_function",
                                                  "input": {"name": "divide"}})
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")
        self.assertEqual(sent[0]["tools"][0]["input_schema"]["required"], ["name"])
        self.assertEqual((reply.text, reply.calls[0]["name"]), ("Let me check.", "find_callers"))


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
