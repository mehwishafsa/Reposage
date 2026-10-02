"""C: functions, structs, comments, #include and calls across .h/.c files."""

import unittest

from tests.helpers import copy_fixture, edges, node, node_ids, scan


class CParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, _ = scan(copy_fixture("c_project"))

    def test_functions_and_structs(self):
        funcs = node_ids(self.graph, "function")
        self.assertIn("main.c::main", funcs)
        self.assertIn("calc.c::power", funcs)
        # prototypes in headers are promises, not definitions
        self.assertFalse(any(f.endswith(".h") for f in (i.split("::")[0] for i in funcs)))
        records = node_ids(self.graph, "record")
        self.assertEqual(records, {"stats.c::Summary", "stats.c::Node"})

    def test_comments_become_docs(self):
        self.assertEqual(node(self.graph, "calc.c::add")["doc"], "Add two numbers.")
        self.assertTrue(node(self.graph, "stats.c::read_numbers")["doc"].startswith("Ask for up to"))
        self.assertTrue(node(self.graph, "main.c")["doc"].startswith("Simple calculator lab program."))

    def test_includes(self):
        imports = edges(self.graph, "imports")
        self.assertIn(("main.c", "lib/calc.h"), imports)       # path next to the file
        self.assertIn(("calc.c", "lib/calc.h"), imports)       # found by unique file name
        self.assertIn(("main.c", "stats.h"), imports)
        self.assertFalse(any(t.endswith("stdio.h") for _, t in imports))

    def test_calls_reach_the_c_file_behind_the_header(self):
        calls = edges(self.graph, "calls")
        self.assertIn(("main.c::run_choice", "calc.c::add"), calls)
        self.assertIn(("main.c::run_choice", "stats.c::run_stats"), calls)
        self.assertIn(("calc.c::power", "calc.c::multiply"), calls)
        self.assertIn(("stats.c::average", "stats.c::sum_array"), calls)
        # library calls (printf, scanf) and function pointers are not linked
        self.assertFalse(any(s == "stats.c::use_pointer" for s, _ in calls))
        self.assertFalse(any("printf" in t for _, t in calls))


if __name__ == "__main__":
    unittest.main()
