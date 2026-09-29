"""/reposage:chat: retrieval, the context pack, and Answer Paths."""

import contextlib
import io
import json
import os
import unittest

from reposage import chat
from reposage.rag import Index, tokenize
from tests.helpers import copy_fixture, scan

S = "shop/services.py"
M = "shop/models.py"


class TokenizeTest(unittest.TestCase):
    def test_splits_names_and_drops_filler(self):
        self.assertEqual(tokenize("How does retryFromError work?"), ["retry", "error"])   # "from" is filler
        self.assertEqual(tokenize("max_retries retried"), ["max", "retry", "retry"])
        self.assertEqual(tokenize("user logs in"), ["user", "log"])      # "in" is filler


class RetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = copy_fixture("py_project")
        cls.graph, _ = scan(cls.repo)

    def test_finds_code_and_follows_calls(self):
        r = Index(self.repo, self.graph).retrieve("How is the order total calculated?")
        ids = [h.id for h in r.hits]
        self.assertIn(f"{M}::Order.total", ids[:2])                       # among the best matches
        self.assertIn(f"{M}::Order.price_of", ids)                        # pulled in via the call graph
        self.assertIn((f"{M}::Order.total", f"{M}::Order.price_of"),
                      {(e["source"], e["target"]) for e in r.links})

    def test_summaries_make_plain_english_match(self):
        graph = json.loads(json.dumps(self.graph))
        for n in graph["nodes"]:
            if n["id"] == f"{S}::refund":
                n["summary"] = "Gives the customer their money back."
        hits = Index(self.repo, graph).retrieve("how do customers get money back")
        self.assertEqual(hits.hits[0].id, f"{S}::refund")

    def test_unrelated_question_finds_nothing(self):
        self.assertEqual(Index(self.repo, self.graph).retrieve("kubernetes helm charts").hits, [])


class PackAndPathTest(unittest.TestCase):
    def setUp(self):
        self.repo = copy_fixture("py_project")
        self.graph, _ = scan(self.repo)
        self.pack = chat.ask(self.repo, self.graph, "How is the order total calculated?")
        with open(os.path.join(self.repo, ".reposage", "ai", "chat-context.json"), encoding="utf-8") as f:
            self.ctx = json.load(f)
        self.n = {it["id"]: it["n"] for it in self.ctx["items"]}

    def test_pack_has_numbered_code_with_line_numbers(self):
        self.assertIn(f"## [{self.n[f'{M}::Order.total']}] Order.total  (method", self.pack)
        self.assertIn(f"- where: {M}:10-12", self.pack)
        self.assertIn("   12 |         return sum(self.price_of(i) for i in self.items)", self.pack)
        self.assertIn("## Calls between these items", self.pack)
        self.assertLess(len(self.pack), chat.MAX_PACK_CHARS + 2000)      # stays small

    def test_confident_path(self):
        rec = chat.save_path(self.repo, [f"{self.n[f'{M}::Order.total']}:adds up the prices",
                                         f"{self.n[f'{M}::Order.price_of']}:reads one price"], "Sums prices.")
        self.assertEqual((rec["confidence"], rec["hops"]), ("high", ["high"]))
        self.assertEqual(rec["steps"][0]["label"], "adds up the prices")
        self.assertEqual(chat.load_answers(self.repo)[0]["id"], rec["id"])

    def test_guessed_and_unlinked_steps_lower_confidence(self):
        # checkout -> Order.total is a guessed (name-only) call in the fixture
        pack = chat.ask(self.repo, self.graph, "checkout order total")
        with open(os.path.join(self.repo, ".reposage", "ai", "chat-context.json"), encoding="utf-8") as f:
            n = {it["id"]: it["n"] for it in json.load(f)["items"]}
        self.assertIn("GUESSED", pack)
        rec = chat.save_path(self.repo, [f"{n[f'{S}::checkout']}:starts", f"{n[f'{M}::Order.total']}:sums"])
        self.assertEqual((rec["confidence"], rec["hops"]), ("medium", ["medium"]))
        self.assertIn("guessed", rec["confidence_note"])

    def test_bad_steps(self):
        rec = chat.save_path(self.repo, ["1:ok", "99:nope", "x:bad"])
        self.assertEqual(len(rec["steps"]), 1)
        self.assertEqual(len(rec["problems"]), 2)
        with self.assertRaises(ValueError):
            chat.save_path(self.repo, ["42:nothing valid"])

    def test_path_reaches_the_dashboard(self):
        from reposage.__main__ import main
        from reposage.dashboard.build import build_data
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["chat", "path", "--repo", self.repo, "--no-open", "--answer", "Sums prices.",
                         f"{self.n[f'{M}::Order.total']}:adds up", f"{self.n[f'{M}::Order.price_of']}:one price"])
        self.assertEqual(code, 0)
        self.assertIn("confidence high", out.getvalue())
        self.assertIn("dashboard.html#answer=", out.getvalue())
        with open(os.path.join(self.repo, ".reposage", "graph.json"), encoding="utf-8") as f:
            data = build_data(json.load(f), self.repo)
        ans = data["answers"][0]
        self.assertEqual(ans["question"], "How is the order total calculated?")
        self.assertEqual([data["sym"]["qual"][st["i"]] for st in ans["steps"]], ["Order.total", "Order.price_of"])


if __name__ == "__main__":
    unittest.main()
