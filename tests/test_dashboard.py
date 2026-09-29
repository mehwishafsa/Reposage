"""Dashboard: compact data format, HTML packing, and the layer guesser."""

import contextlib
import io
import json
import os
import re
import unittest

from reposage.dashboard.build import build_data, render_html
from reposage.layers import guess_layer
from tests.helpers import copy_fixture, scan


class LayerGuessTest(unittest.TestCase):
    def test_examples(self):
        cases = {
            "src/components/Button.tsx": "UI",
            "app/routes/users.py": "API",
            "src/main/java/com/shop/App.java": "API",
            "shop/services.py": "Service",
            "src/main/java/com/shop/repo/OrderRepository.java": "Data",
            "shop/models.py": "Data",
            "shop/utils.py": "Utility",
            "tests/test_login.py": "Tests",
            "src/user.test.ts": "Tests",
            "src/test/java/com/shop/OrderTest.java": "Tests",
            "reposage/scanner.py": "Unclassified",    # no hint -> honest "don't know"
        }
        for path, layer in cases.items():
            self.assertEqual(guess_layer(path), layer, path)


class DashboardDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = copy_fixture("java_project")
        cls.graph, _ = scan(cls.repo)
        cls.data = build_data(cls.graph, cls.repo)

    def test_counts_match_graph(self):
        d, g = self.data, self.graph
        self.assertEqual(len(d["files"]["path"]), g["stats"]["files"])
        n_syms = sum(v for k, v in g["stats"]["nodes"].items() if k != "file")
        self.assertEqual(len(d["sym"]["name"]), n_syms)
        n_calls = g["stats"]["edges"]["calls"]
        # calls whose caller is a whole file (top-level code) are not symbol links
        file_callers = sum(1 for e in g["edges"] if e["type"] == "calls" and "::" not in e["source"])
        self.assertEqual(len(d["calls"]) // 3, n_calls - file_callers)

    def test_all_indexes_are_valid(self):
        d = self.data
        n_files, n_syms = len(d["files"]["path"]), len(d["sym"]["name"])
        for column in d["sym"].values():
            self.assertEqual(len(column), n_syms)       # columns line up
        self.assertTrue(all(0 <= f < n_files for f in d["sym"]["file"]))
        self.assertTrue(all(-1 <= p < n_syms for p in d["sym"]["parent"]))
        self.assertTrue(all(0 <= x < n_syms for k, x in enumerate(d["calls"]) if k % 3 != 2))
        self.assertTrue(all(0 <= x < n_files for k, x in enumerate(d["flinks"]) if k % 4 < 2))

    def test_file_links_merge_imports_and_calls(self):
        d = self.data
        paths = d["files"]["path"]
        links = {(paths[d["flinks"][k]], paths[d["flinks"][k + 1]]): (d["flinks"][k + 2], d["flinks"][k + 3])
                 for k in range(0, len(d["flinks"]), 4)}
        base = "src/main/java/com/shop/"
        imp, calls = links[(base + "service/OrderService.java", base + "repo/OrderRepository.java")]
        self.assertEqual(imp, 1)
        self.assertGreaterEqual(calls, 2)          # new OrderRepository() + repo.save()

    def test_ai_layer_wins_over_guess(self):
        graph = json.loads(json.dumps(self.graph))
        target = "src/main/java/com/shop/model/Order.java"
        for n in graph["nodes"]:
            if n["id"] == target:
                n["layer"] = "Service"
        d = build_data(graph, self.repo)
        i = d["files"]["path"].index(target)
        self.assertEqual(d["groups"][d["files"]["group"][i]], "Service")
        self.assertEqual(d["files"]["ai"][i], 1)

    def test_html_is_self_contained_and_safe(self):
        graph = json.loads(json.dumps(self.graph))
        graph["nodes"][1]["doc"] = "evil </script><script>alert(1)</script>"
        html = render_html(build_data(graph, self.repo))
        self.assertNotIn("__REPOSAGE_DATA__", html)
        self.assertNotIn("/*__REPOSAGE_VENDOR__*/", html)
        self.assertIn("d3-force", html)                     # libraries inlined
        self.assertNotRegex(html, r"<script[^>]+src=")      # nothing loaded from outside
        self.assertNotRegex(html, r"<link[^>]+href=")
        # The data block must survive intact: exactly one closing tag after it.
        block = re.search(r'<script type="application/json" id="rs-data">(.*?)</script>', html, re.S)
        payload = json.loads(block.group(1))
        self.assertIn("evil </script>", json.dumps(payload))

    def test_same_graph_same_html(self):
        a = render_html(build_data(self.graph, self.repo))
        b = render_html(build_data(self.graph, self.repo))
        self.assertEqual(a, b)


class DashboardCommandTest(unittest.TestCase):
    def test_writes_file_without_browser(self):
        from reposage.__main__ import run_dashboard
        repo = copy_fixture("js_project")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run_dashboard(repo, open_browser=False), 0)
        path = os.path.join(repo, ".reposage", "dashboard.html")
        self.assertTrue(os.path.getsize(path) > 20_000)
        self.assertIn("Open this in a browser: file://", out.getvalue())
        with open(os.path.join(repo, ".reposage", ".gitignore"), encoding="utf-8") as f:
            self.assertIn("dashboard.html", f.read())


if __name__ == "__main__":
    unittest.main()
