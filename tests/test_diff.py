"""/reposage:diff: changed functions, ripple effect, tests and risk.

Each test makes a tiny git repository from the py_project fixture, commits
it, edits a file and runs the analysis.
"""

import os
import subprocess
import unittest

from reposage import diff
from tests.helpers import copy_fixture, scan

M, S, U = "shop/models.py", "shop/services.py", "shop/utils.py"


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
                   cwd=repo, check=True, capture_output=True)


def make_repo():
    repo = copy_fixture("py_project")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def replace(repo, path, old, new):
    full = os.path.join(repo, path)
    with open(full, encoding="utf-8") as f:
        text = f.read()
    assert old in text, old
    with open(full, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))


def run(repo, base=None):
    graph, _ = scan(repo)
    return diff.analyze(repo, graph, diff.read_changes(repo, base))


class DiffTest(unittest.TestCase):
    def test_body_change_ripples_to_callers(self):
        repo = make_repo()
        replace(repo, M, 'return item["price"]', 'return item["price"] * item.get("qty", 1)')
        a = run(repo)
        self.assertEqual(list(a["changed"]), [f"{M}::Order.price_of"])
        self.assertFalse(a["changed"][f"{M}::Order.price_of"]["signature_changed"])
        depths = {x["id"]: (x["depth"], x["guessed"]) for x in a["affected"]}
        self.assertEqual(depths[f"{M}::Order.total"], (1, False))       # self.price_of(...)
        self.assertEqual(depths[f"{S}::checkout"], (2, True))           # order.total() is a guessed link
        self.assertEqual(a["risk"], "Medium")                           # untested, but has callers
        pack = diff.render_pack(repo, a)
        self.assertIn('+   15 |         return item["price"] * item.get("qty", 1)', pack)  # changed line marked
        self.assertIn('-        return item["price"]', pack)                              # and the old line
        self.assertIn("GUESSED", pack)
        self.assertIn("tests: NONE found", pack)

    def test_signature_change_is_high_risk(self):
        repo = make_repo()
        replace(repo, M, "def price_of(self, item):", "def price_of(self, item, currency):")
        a = run(repo)
        self.assertTrue(a["changed"][f"{M}::Order.price_of"]["signature_changed"])
        self.assertEqual(a["risk"], "High")

    def test_removed_function_still_called(self):
        repo = make_repo()
        replace(repo, U, 'def slugify(text):\n    """Make a URL-friendly slug."""\n    return _clean(text).replace(" ", "-")\n\n\n', "")
        a = run(repo)
        self.assertEqual([r["qual"] for r in a["removed"]], ["slugify"])
        self.assertEqual(a["still_called"][0]["caller"], f"{S}::checkout")
        self.assertEqual(a["risk"], "High")
        self.assertIn("STILL CALLED by checkout", diff.render_pack(repo, a))

    def test_tests_that_reach_the_change(self):
        repo = make_repo()
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "tests", "test_models.py"), "w") as f:
            f.write("from shop.models import Order\n\n\ndef test_total():\n"
                    "    assert Order([{'price': 2}]).total() == 2\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "tests")
        replace(repo, M, 'return item["price"]', 'return item["price"] + 0')
        a = run(repo)
        self.assertEqual(a["tests_for"][f"{M}::Order.price_of"], ["tests/test_models.py::test_total"])
        self.assertNotIn("tests/test_models.py::test_total", [x["id"] for x in a["affected"]])  # tests aren't "affected"

    def test_comment_only_edit_is_not_a_code_change(self):
        repo = make_repo()
        with open(os.path.join(repo, U), "a") as f:
            f.write("\n# just a note\n")
        a = run(repo)
        self.assertEqual((a["changed"], a["file_level"]), ({}, []))
        self.assertEqual(a["risk"], "Low")

    def test_base_branch_includes_commits_and_new_files(self):
        repo = make_repo()
        git(repo, "checkout", "-q", "-b", "feature")
        replace(repo, U, "return text.strip().lower()", "return text.strip().casefold()")
        git(repo, "commit", "-q", "-am", "casefold")
        with open(os.path.join(repo, "shop", "extra.py"), "w") as f:
            f.write("def brand_new():\n    return 1\n")
        a = run(repo, base="main")
        self.assertIn(f"{U}::_clean", a["changed"])                      # committed on the branch
        self.assertEqual(a["changed"]["shop/extra.py::brand_new"]["status"], "added")   # untracked
        self.assertEqual(a["label"], "changes since this branch left main")

    def test_view_reaches_the_dashboard(self):
        from reposage.dashboard.build import build_data
        repo = make_repo()
        replace(repo, M, 'return item["price"]', 'return item["price"] + 0')
        a = run(repo)
        diff.save_view(repo, a)
        diff.add_summary(repo, "price_of now adds zero.")
        graph, _ = scan(repo)
        view = build_data(graph, repo)["answers"][0]
        self.assertEqual((view["kind"], view["risk"], view["answer"]), ("diff", "Medium", "price_of now adds zero."))
        roles = {build_data(graph, repo)["sym"]["qual"][n["i"]]: n["role"] for n in view["nodes"]}
        self.assertEqual(roles["Order.price_of"], "changed")
        self.assertEqual(roles["Order.total"], "direct")

    def test_test_files_show_up_as_tests(self):
        from reposage.dashboard.build import build_data
        repo = make_repo()
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "tests", "test_top.py"), "w") as f:   # top-level test code
            f.write("from shop.models import Order\n\nassert Order([]).total() == 0\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "tests")
        replace(repo, M, 'return item["price"]', 'return item["price"] + 0')
        diff.save_view(repo, run(repo))
        graph, _ = scan(repo)
        data = build_data(graph, repo)
        tests = data["answers"][0]["tests"]
        self.assertEqual([(t["type"], data["files"]["path"][t["i"]]) for t in tests], [("file", "tests/test_top.py")])

    def test_not_a_git_repo(self):
        repo = copy_fixture("py_project")
        with self.assertRaises(diff.DiffError):
            diff.read_changes(repo)


if __name__ == "__main__":
    unittest.main()
