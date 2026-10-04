"""Same code must give the same graph; re-scans must be incremental."""

import json
import os
import shutil
import unittest

from tests.helpers import copy_fixture, node, scan


def graph_bytes(repo: str) -> bytes:
    with open(os.path.join(repo, ".reposage", "graph.json"), "rb") as f:
        return f.read()


class DeterminismTest(unittest.TestCase):
    def test_same_code_same_graph(self):
        for fixture in ("py_project", "js_project", "java_project"):
            a, b = copy_fixture(fixture), copy_fixture(fixture)   # different folders
            scan(a)
            scan(b)
            self.assertEqual(graph_bytes(a), graph_bytes(b), fixture)

    def test_cached_scan_equals_full_scan(self):
        repo = copy_fixture("js_project")
        scan(repo)
        first = graph_bytes(repo)
        _, report = scan(repo)
        self.assertIn("0 new/changed, 6 unchanged", report)
        self.assertEqual(graph_bytes(repo), first)
        scan(repo, full=True)
        self.assertEqual(graph_bytes(repo), first)

    def test_incremental_reparses_only_changed_files(self):
        repo = copy_fixture("py_project")
        scan(repo)
        with open(os.path.join(repo, "shop", "utils.py"), "a", encoding="utf-8") as f:
            f.write("\n\ndef shout(text):\n    return slugify(text).upper()\n")
        os.remove(os.path.join(repo, "shop", "__init__.py"))
        graph, report = scan(repo)
        self.assertIn("1 new/changed, 2 unchanged (cached), 1 removed", report)
        self.assertIn("shop/utils.py::shout", {n["id"] for n in graph["nodes"]})

    def test_ai_fields_survive_rescan_only_if_file_unchanged(self):
        repo = copy_fixture("py_project")
        graph, _ = scan(repo)
        # Pretend an AI agent wrote summaries into the graph.
        for n in graph["nodes"]:
            if n["id"] in ("shop/models.py::Order", "shop/utils.py::slugify"):
                n["summary"] = "AI summary"
                n["layer"] = "Service"
        path = os.path.join(repo, ".reposage", "graph.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(graph, f)
        with open(os.path.join(repo, "shop", "utils.py"), "a", encoding="utf-8") as f:
            f.write("\n# changed\n")
        graph, _ = scan(repo)
        self.assertEqual(node(graph, "shop/models.py::Order")["summary"], "AI summary")
        stale = node(graph, "shop/utils.py::slugify")
        self.assertEqual(stale["summary"], "AI summary")      # kept as a hint...
        self.assertTrue(stale["ai_stale"])                    # ...but marked out of date
        self.assertNotIn("ai_stale", node(graph, "shop/models.py::Order"))

    def test_gitignore_created_once(self):
        repo = copy_fixture("py_project")
        scan(repo)
        gi = os.path.join(repo, ".reposage", ".gitignore")
        with open(gi, encoding="utf-8") as f:
            self.assertIn("cache/", f.read())
        with open(gi, "w", encoding="utf-8") as f:
            f.write("*\n")                           # user chooses "keep private"
        scan(repo)
        with open(gi, encoding="utf-8") as f:
            self.assertEqual(f.read(), "*\n")

    def test_skips_node_modules_and_minified(self):
        repo = copy_fixture("js_project")
        os.makedirs(os.path.join(repo, "node_modules", "lib"))
        shutil.copy(os.path.join(repo, "src", "util.js"),
                    os.path.join(repo, "node_modules", "lib", "index.js"))
        shutil.copy(os.path.join(repo, "src", "util.js"),
                    os.path.join(repo, "src", "vendor.min.js"))
        graph, _ = scan(repo)
        self.assertEqual(graph["stats"]["files"], 6)


if __name__ == "__main__":
    unittest.main()
