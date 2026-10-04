"""The "Ask RepoSage" agent: tools, step limit, citations, no-AI fallback, cache."""

import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("REPOSAGE_DATA", tempfile.mkdtemp(prefix="reposage-web-test-"))
os.environ.setdefault("AI_PROVIDER", "fake")
os.environ.setdefault("AI_FALLBACK", "none")

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app, chat_limiter, limiter  # noqa: E402
from app.services import chat_agent, projects  # noqa: E402
from app.services.llm import FakeProvider, RateLimited, llm  # noqa: E402

client = TestClient(app)


def make_project(sample: str) -> str:
    pid = client.post("/api/projects/sample", json={"sample": sample}).json()["id"]
    for _ in range(200):
        if client.get(f"/api/projects/{pid}").json()["status"] == "ready":
            return pid
        time.sleep(0.05)
    raise AssertionError("not ready")


def ask(pid: str, question: str, **body) -> dict:
    r = client.post(f"/api/projects/{pid}/chat", json={"question": question, **body})
    assert r.status_code == 200, r.text
    qid = r.json()["id"]
    for _ in range(200):
        job = client.get(f"/api/projects/{pid}/chat/{qid}").json()
        if job["status"] != "working":
            job["id"] = qid
            return job
        time.sleep(0.03)
    raise AssertionError("no answer")


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        limiter.hits.clear()
        cls.calc = make_project("calculator-c")
        cls.auth = make_project("miniauth")

    def setUp(self):
        llm.reset()
        llm.sleep = lambda s: None
        FakeProvider.fail_next.clear()
        chat_limiter.hits.clear()
        config.AI_PROVIDER, config.AI_FALLBACK = "fake", "none"

    def tearDown(self):
        config.AI_PROVIDER, config.AI_FALLBACK = "fake", "none"
        llm.reset()

    def test_tools_read_only_and_useful(self):
        t = chat_agent.Toolbox(projects.project_dir(self.calc))
        text, step = t.search_code("How does the calculator divide?")
        self.assertEqual(step["title"], 'Searched "calculator divide"')
        self.assertEqual(step["refs"][0]["label"], "divide()")
        text, step = t.read_function("divide")
        self.assertIn("  25 |     return a / b;", text)
        self.assertEqual(t.find_callers("divide")[1]["detail"], "Called by run_choice()")
        self.assertIn("add()", t.find_callees("run_choice")[1]["detail"])
        text, step = t.get_flowchart("run_choice")
        self.assertIn("[switch] line 26", text)
        self.assertEqual(step["detail"], "2 decisions, 1 menu")
        self.assertIn("not found", t.read_function("no_such_function")[0])
        self.assertIn("not found", t.read_file("../../reposage.db")[0])

    def test_synonyms_help_beginner_words(self):
        t = chat_agent.Toolbox(projects.project_dir(self.calc))
        self.assertEqual(t.search_code("how does the menu work")[1]["refs"][0]["label"], "run_choice()")
        t2 = chat_agent.Toolbox(projects.project_dir(make_project("todo-python")))
        self.assertEqual(t2.search_code("How are tasks saved?")[1]["refs"][0]["label"], "save_tasks()")

    def test_agent_shows_steps_and_cites_real_lines(self):
        job = ask(self.calc, "How does the calculator divide?")
        self.assertEqual(job["mode"], "ai")
        titles = [s["title"] for s in job["steps"]]
        self.assertEqual(titles, ['Searched "calculator divide"', "Read divide() in calc.c",
                                  "Checked who calls divide()", "Wrote the answer"])
        self.assertEqual(job["citations"], [{"file": "calc.c", "start": 23, "end": 26}])
        self.assertEqual(job["focus"]["function_id"], "calc.c::divide")
        self.assertEqual(len(job["followups"]), 3)

    def test_made_up_citations_are_removed(self):
        t = chat_agent.Toolbox(projects.project_dir(self.calc))
        text, cites = chat_agent.clean_citations(
            "See [calc.c:23-26], [calc.c:999], [nope.c:3] and [main.c:26].", t)
        self.assertEqual(text, "See [calc.c:23-26], and [main.c:26].")
        self.assertEqual([c["file"] for c in cites], ["calc.c", "main.c"])

    def test_step_limit(self):
        """An AI that never stops using tools is cut off after CHAT_MAX_AI_CALLS."""
        calls = []

        def endless(system, prompt):
            calls.append(1)
            return json.dumps({"thought": "more", "action": "search_code", "input": {"query": f"x{len(calls)}"}})
        old = FakeProvider.handlers["chat_agent"]
        FakeProvider.handlers["chat_agent"] = endless
        try:
            job = ask(self.calc, "endless question about power")
        finally:
            FakeProvider.handlers["chat_agent"] = old
        self.assertEqual(len(calls), chat_agent.MAX_AI_CALLS)
        self.assertEqual(job["mode"], "no_ai")               # still shows the best code
        self.assertTrue(job["answer"])

    def test_daily_limit_mid_question_falls_back(self):
        FakeProvider.fail_next.append(RateLimited(daily=True))
        job = ask(self.auth, "How is the password checked?")
        self.assertEqual((job["mode"], job["reason"]), ("no_ai", "resting"))
        self.assertIn("today's limit", job["message"])
        self.assertIn("verify_password()", job["answer"])
        self.assertEqual(job["focus"]["function_id"], "auth.py::verify_password")

    def test_without_ai_loop_question(self):
        config.AI_PROVIDER = "gemini"                    # no key here
        llm.reset()
        job = ask(self.calc, "What does the loop in main do?")
        self.assertEqual((job["mode"], job["reason"]), ("no_ai", "off"))
        self.assertIn("The loop at [main.c:45]", job["answer"])
        self.assertIn("`choice` is not equal to `0`", job["answer"])
        self.assertEqual([s["tool"] for s in job["steps"]],
                         ["find_construct", "search_code", "read_function", "find_callers", "get_flowchart"])
        self.assertEqual(job["steps"][0]["detail"], "Found 1 while loop: main()")

    def test_general_question_uses_overview(self):
        config.AI_PROVIDER = "gemini"
        llm.reset()
        job = ask(self.auth, "What does this program do?")
        self.assertIn("project_overview", [s["tool"] for s in job["steps"]])
        self.assertIn("Start reading at `route()` [main.py:25]", job["answer"])

    def test_answers_are_cached(self):
        first = ask(self.auth, "What happens when a user logs in?")
        FakeProvider.calls.clear()
        again = ask(self.auth, "what happens when a user logs in?")      # same question, other case
        self.assertEqual(again["answer"], first["answer"])
        self.assertEqual(FakeProvider.calls, [])                       # no new AI calls

    def test_explain_simpler_keeps_only_known_citations(self):
        job = ask(self.calc, "How does power work?")
        res = {"status": "thinking"}
        for _ in range(100):
            res = client.post(f"/api/projects/{self.calc}/chat/{job['id']}/simpler").json()
            if res["status"] != "thinking":
                break
            time.sleep(0.03)
        self.assertEqual(res["status"], "done")
        self.assertIn("recipe card", res["answer"])
        self.assertIn("[calc.c:", res["answer"])

    def test_for_loop_concept_question(self):
        """'for' is a stop word for the search, so it used to find main()'s while loop."""
        config.AI_PROVIDER = "gemini"
        llm.reset()
        job = ask(self.calc, "what is for loop used for?")
        self.assertEqual([s["tool"] for s in job["steps"]], ["glossary", "find_construct", "read_function"])
        self.assertTrue(job["answer"].startswith("A for loop repeats some steps"))
        self.assertIn("In your code: `power()` uses one [calc.c:33-35].", job["answer"])
        self.assertIn("It starts with `i = 0`", job["answer"])           # the loop box, not `i = 0`'s
        self.assertEqual(job["focus"]["function_id"], "calc.c::power")
        simpler = client.post(f"/api/projects/{self.calc}/chat/{job['id']}/simpler").json()
        self.assertIn("do this 10 times", simpler["answer"])

    def test_concept_with_ai_gets_the_concept_instruction(self):
        seen = []
        old = FakeProvider.handlers["chat_agent"]
        FakeProvider.handlers["chat_agent"] = lambda s, p: (seen.append(p), old(s, p))[1]
        try:
            job = ask(self.calc, "Why use a switch statement?")
        finally:
            FakeProvider.handlers["chat_agent"] = old
        self.assertEqual(job["steps"][1]["detail"], "Found 1 switch: run_choice()")
        self.assertIn("first explain the idea", seen[0])
        self.assertIn("glossary(switch statement):", seen[0])

    def test_specific_code_question_is_not_a_concept(self):
        config.AI_PROVIDER = "gemini"
        llm.reset()
        job = ask(self.calc, "What does the for loop in power do?")
        self.assertNotEqual(job["steps"][0]["tool"], "glossary")
        self.assertEqual(job["steps"][0]["detail"], "Found 1 for loop: power()")
        self.assertIn("The loop at [calc.c:33]", job["answer"])

    def test_suggestions_and_limits(self):
        qs = client.get(f"/api/projects/{self.calc}/chat-suggestions").json()["questions"]
        self.assertEqual(qs[:2], ["What does this program do?", "What does the loop in main() do?"])
        self.assertEqual(client.post(f"/api/projects/{self.calc}/chat", json={"question": "  "}).status_code, 400)
        old = chat_limiter.limit
        chat_limiter.limit = 1
        try:
            client.post(f"/api/projects/{self.calc}/chat", json={"question": "one"})
            self.assertEqual(client.post(f"/api/projects/{self.calc}/chat", json={"question": "two"}).status_code, 429)
        finally:
            chat_limiter.limit = old


if __name__ == "__main__":
    unittest.main()
