"""JavaScript / TypeScript / TSX: definitions, imports and call resolution."""

import unittest

from tests.helpers import copy_fixture, edges, node, node_ids, scan

API, IDX, UTIL = "src/api.ts", "src/index.ts", "src/util.js"
FMT, BTN, LEG = "src/format.ts", "src/components/Button.tsx", "src/legacy.cjs"


class JavaScriptParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, _ = scan(copy_fixture("js_project"))

    def test_languages(self):
        self.assertEqual(self.graph["stats"]["languages"],
                         {"javascript": 2, "typescript": 4})

    def test_definitions(self):
        ids = node_ids(self.graph)
        for expected in (f"{API}::Api", f"{API}::Api.fetchUser",
                         f"{API}::Api.onError",          # arrow-function class field
                         f"{API}::User",                 # TS interface
                         f"{UTIL}::helper",              # const helper = () => ...
                         f"{UTIL}::warn",                # exports.warn = function
                         f"{BTN}::Button", f"{FMT}::format"):
            self.assertIn(expected, ids)
        self.assertEqual(node(self.graph, f"{API}::User")["type"], "interface")
        self.assertEqual(node(self.graph, f"{API}::Api.onError")["type"], "method")

    def test_doc_comments(self):
        self.assertEqual(node(self.graph, f"{API}::Api")["doc"], "Talks to the backend server.")
        self.assertEqual(node(self.graph, f"{IDX}::main")["doc"], "Program entry point.")

    def test_imports(self):
        imports = edges(self.graph, "imports")
        self.assertIn((IDX, API), imports)
        self.assertIn((IDX, FMT), imports)     # "./format.js" resolves to format.ts
        self.assertIn((IDX, UTIL), imports)
        self.assertIn((LEG, UTIL), imports)    # require("./util")
        self.assertEqual(node(self.graph, BTN)["external_imports"], ["react"])

    def test_calls(self):
        calls = edges(self.graph, "calls")
        expected = {
            (f"{IDX}::main", f"{API}::Api"),              # new Api()
            (f"{IDX}::main", f"{UTIL}::log"),             # util.log (namespace import)
            (f"{IDX}::main", f"{FMT}::format"),           # default import
            (f"{API}::Api.fetchUser", f"{API}::Api.request"),   # this.request()
            (f"{API}::Api.onError", f"{API}::Api.request"),
            (f"{UTIL}::log", f"{UTIL}::helper"),
            (f"{UTIL}::warn", f"{UTIL}::log"),
            (LEG, f"{UTIL}::log"),                        # top-level call in a file
            (f"{BTN}::Button", f"{BTN}::track"),          # call inside JSX
        }
        self.assertTrue(expected <= calls, expected - calls)
        # `helper` comes from "some-library", NOT from util.js's helper.
        self.assertNotIn((f"{IDX}::main", f"{UTIL}::helper"), calls)
        # console.log / fetch are not in the repo -> no edges.
        self.assertFalse(any("console" in t or t.endswith("::fetch") for _, t in calls))


if __name__ == "__main__":
    unittest.main()


class ResolverRulesTest(unittest.TestCase):
    """Small focused checks of the call-resolution rules."""

    def test_global_receiver_not_linked(self):
        from reposage.graph_builder import build_graph
        from reposage.parsers import parser_for
        src = {
            "a.ts": b"export class Widget {}\n",
            "b.ts": b"export function f() { return new globalThis.Widget(); }\n",
        }
        facts = {p: parser_for(p).parse(p, data) for p, data in src.items()}
        graph = build_graph(facts, {p: "x" for p in src})
        self.assertEqual([e for e in graph["edges"] if e["type"] == "calls"], [])
