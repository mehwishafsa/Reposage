"""Python: definitions, docstrings, imports and call resolution."""

import unittest

from reposage.parsers import parser_for
from tests.helpers import copy_fixture, edges, node, node_ids, scan

M = "shop/models.py"
S = "shop/services.py"
U = "shop/utils.py"


class PythonParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, _ = scan(copy_fixture("py_project"))

    def test_definitions_and_kinds(self):
        self.assertEqual(node_ids(self.graph, "class"), {f"{M}::Order"})
        self.assertIn(f"{M}::Order.total", node_ids(self.graph, "method"))
        self.assertIn(f"{M}::Order.empty", node_ids(self.graph, "method"))  # decorated
        # A function nested in a function is a "function" with a parent.
        audit = node(self.graph, f"{S}::refund.audit")
        self.assertEqual(audit["type"], "function")
        self.assertEqual(audit["parent"], f"{S}::refund")

    def test_line_numbers_include_decorators(self):
        refund = node(self.graph, f"{S}::refund")
        self.assertEqual(refund["start_line"], 25)   # the @retry line
        self.assertEqual(refund["signature"], "def refund(order_id):")

    def test_docstrings(self):
        self.assertEqual(node(self.graph, f"{M}::Order")["doc"],
                         "An order placed by a customer.")
        self.assertEqual(node(self.graph, M)["doc"], "Data models.")
        self.assertEqual(node(self.graph, f"{M}::Order.price_of")["doc"], "")

    def test_imports(self):
        imports = edges(self.graph, "imports")
        self.assertIn((S, M), imports)             # from .models import Order
        self.assertIn((S, U), imports)             # from . import utils
        self.assertEqual(node(self.graph, S)["external_imports"], ["logging"])

    def test_calls(self):
        calls = edges(self.graph, "calls")
        expected = {
            (f"{S}::checkout", f"{M}::Order"),              # aliased import + constructor
            (f"{S}::checkout", f"{U}::slugify"),            # utils.slugify()
            (f"{M}::Order.total", f"{M}::Order.price_of"),  # self.price_of()
            (f"{S}::refund", f"{S}::refund.audit"),         # nested function
            (f"{S}::refund.audit", f"{U}::_clean"),         # from x import *
            (f"{U}::slugify", f"{U}::_clean"),              # same file
        }
        self.assertTrue(expected <= calls, expected - calls)
        # log.info() is a library call and must not be linked to anything.
        self.assertFalse(any(t.endswith("info") for _, t in calls))

    def test_confidence(self):
        conf = {(e["source"], e["target"]): e["confidence"]
                for e in self.graph["edges"] if e["type"] == "calls"}
        self.assertEqual(conf[(f"{S}::checkout", f"{U}::slugify")], "high")
        # `order.total()`: we don't know order's type, only that `total` is unique.
        self.assertEqual(conf[(f"{S}::checkout", f"{M}::Order.total")], "medium")

    def test_syntax_errors_do_not_crash(self):
        facts = parser_for("bad.py").parse("bad.py", b"def ok():\n    pass\n\ndef broken(:\n")
        self.assertTrue(facts.has_errors)
        self.assertIn("bad.py::ok", [d.id for d in facts.definitions])


if __name__ == "__main__":
    unittest.main()
