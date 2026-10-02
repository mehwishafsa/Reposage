"""Flowcharts and pattern-based explanations (no AI) for all five languages."""

import os
import tempfile
import time
import unittest

os.environ.setdefault("REPOSAGE_DATA", tempfile.mkdtemp(prefix="reposage-web-test-"))
os.environ.setdefault("AI_PROVIDER", "fake")

from app.services import explain  # noqa: E402
from app.services.flowchart import build_flowchart  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "..", "samples")


def chart(path: str, code: str, name: str) -> dict:
    lines = code.splitlines()
    start = next(i for i, l in enumerate(lines, 1) if name + "(" in l or f"def {name}" in l)
    depth, end = 0, start
    if path.endswith(".py"):
        indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
        end = start
        for i in range(start, len(lines)):
            l = lines[i]
            if l.strip() and len(l) - len(l.lstrip()) <= indent:
                break
            if l.strip():
                end = i + 1
    else:
        for i in range(start - 1, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if depth == 0 and "{" in "".join(lines[start - 1:i + 1]):
                end = i + 1
                break
    r = build_flowchart(path, code.encode(), start, end, name, {})
    assert r is not None, f"no chart for {name}"
    return r


def edges(r: dict) -> set:
    label = {b["id"]: b["label"] for b in r["boxes"]}
    return {(label[e["from"]], e["label"], label[e["to"]]) for e in r["edges"]}


def kinds(r: dict) -> list:
    return [b["kind"] for b in r["boxes"]]


class CFlowchartTest(unittest.TestCase):
    """The calculator sample: menus, switch-case, loops."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SAMPLES, "calculator-c", "main.c")) as fh:
            cls.main_c = fh.read()
        with open(os.path.join(SAMPLES, "calculator-c", "calc.c")) as fh:
            cls.calc_c = fh.read()

    def test_switch_menu(self):
        r = chart("main.c", self.main_c, "run_choice")
        e = edges(r)
        self.assertIn(("which choice?", "4", "b == 0?"), e)
        self.assertIn(("b == 0?", "yes", 'printf("Cannot divide by zero!\\n");'), e)
        self.assertIn(("choice == 6?", "yes", "run_stats();"), e)
        self.assertIn(("run_stats();", "", "return;"), e)
        cases = sorted(x[1] for x in e if x[0] == "which choice?")
        self.assertEqual(cases, ["1", "2", "3", "4", "5", "default"])
        self.assertFalse(any(b["label"].startswith("1\n") for b in r["boxes"]))   # case labels are not code

    def test_while_menu_loop(self):
        r = chart("main.c", self.main_c, "main")
        e = edges(r)
        self.assertIn(("choice != 0?", "yes", 'show_menu();\nscanf("%d", &choice);'), e)
        self.assertIn(("run_choice(choice);", "", "choice != 0?"), e)       # back to the loop test
        self.assertIn(("choice != 0?", "no", 'printf("Bye!\\n");'), e)

    def test_counting_for_loop(self):
        r = chart("calc.c", self.calc_c, "power")
        e = edges(r)
        self.assertIn(("i = 0", "", "i < exponent?"), e)
        self.assertIn(("i < exponent?", "yes", "result = multiply(result, base);"), e)
        self.assertIn(("result = multiply(result, base);", "", "i++"), e)
        self.assertIn(("i++", "", "i < exponent?"), e)
        self.assertIn(("i < exponent?", "no", "return result;"), e)
        loop = next(b for b in r["boxes"] if b["kind"] == "loop")
        self.assertIn("starts with `i = 0`", loop["explain"])
        self.assertIn("`i` is less than `exponent`", loop["explain"])

    def test_fall_through_and_do_while(self):
        code = """int f(int x) {
    do {
        x--;
        if (x == 5) continue;
    } while (x > 0);
    switch (x) {
    case 1:
    case 2: a(); break;
    case 3: b();
    default: c();
    }
    return x;
}"""
        e = edges(chart("t.c", code, "f"))
        self.assertIn(("x > 0?", "yes", "x--;"), e)                       # do-while repeats the body
        self.assertIn(("x == 5?", "yes", "x > 0?"), e)                    # continue -> the test
        self.assertIn(("which x?", "1, 2", "a();"), e)                    # shared cases
        self.assertIn(("b();", "", "c();"), e)                            # no break: falls into default
        self.assertIn(("a();", "", "return x;"), e)


class OtherLanguagesTest(unittest.TestCase):
    def test_python_elif_while_true_try(self):
        code = '''def f(x):
    """Docstring is not a step."""
    while True:
        if x is None:
            return 0
        elif x > 2:
            break
        else:
            x += 1
    try:
        g(x)
    except ValueError:
        h()
    for item in items:
        print(item)
    return x
'''
        r = chart("t.py", code, "f")
        e = edges(r)
        labels = [b["label"] for b in r["boxes"]]
        self.assertNotIn('"""Docstring is not a step."""', labels)
        self.assertIn("repeat forever", labels)
        self.assertIn(("x is None?", "no", "x > 2?"), e)
        self.assertIn(("x > 2?", "yes", "try"), e)                        # break leaves the loop
        self.assertIn(("x += 1", "", "repeat forever"), e)
        self.assertIn(("try", "if error", "h()"), e)
        self.assertIn(("next item in items?", "yes", "print(item)"), e)
        self.assertIn(("next item in items?", "no more", "return x"), e)

    def test_javascript_switch_and_arrow(self):
        code = """function f(x) {
  for (const k of keys) { use(k); }
  switch (x) { case 1: a(); break; default: b(); }
  try { risky(); } catch (e) { fix(); }
  return x;
}
const g = (a) => a + 1;"""
        e = edges(chart("t.js", code, "f"))
        self.assertIn(("next k in keys?", "yes", "use(k);"), e)
        self.assertIn(("which x?", "1", "a();"), e)
        self.assertIn(("try", "if error", "fix();"), e)
        r = build_flowchart("t.js", code.encode(), 7, 7, "g", {})
        self.assertEqual(kinds(r), ["start", "return", "end"])

    def test_java_switch_rules_and_enhanced_for(self):
        code = """class A {
  int f(int x) {
    for (String s : names) { System.out.println(s); }
    switch (x) { case 1 -> a(); default -> { b(); } }
    if (x > 1) { return 1; } else { x++; }
    return x;
  }
}"""
        e = edges(chart("A.java", code, "f"))
        self.assertIn(("next s in names?", "yes", "System.out.println(s);"), e)
        self.assertIn(("which x?", "1", "a();"), e)
        self.assertIn(("a();", "", "x > 1?"), e)                           # arrow cases never fall through
        self.assertIn(("x > 1?", "no", "x++;"), e)

    def test_typescript(self):
        code = "function f(n: number): number {\n  while (n > 0) { n--; }\n  return n;\n}"
        e = edges(chart("t.ts", code, "f"))
        self.assertIn(("n > 0?", "yes", "n--;"), e)


class ExplainTest(unittest.TestCase):
    def test_conditions_in_words(self):
        self.assertEqual(explain.condition_words("(b == 0)"), "`b` is equal to `0`")
        self.assertEqual(explain.condition_words("x >= 1 && y != 2"),
                         "`x` is at least `1` and `y` is not equal to `2`")
        self.assertEqual(explain.condition_words("user is None"), "`user` has no value (None)")

    def test_statements(self):
        k = {"add": "Add two numbers."}
        self.assertEqual(explain.statement('scanf("%d", &choice);', "c", k)[0],
                         "Waits for the user to type something and stores it in `choice`.")
        self.assertEqual(explain.statement('printf("Bye!\\n");', "c", k)[0], "Shows a message on the screen: “Bye!”.")
        self.assertIn("with the result of `add(a, b)`", explain.statement('printf("%f", add(a, b));', "c", k)[0])
        self.assertEqual(explain.statement("i++", "c", k)[0], "Adds 1 to `i`.")
        self.assertEqual(explain.statement("double a, b;", "c", k)[0],
                         "Makes variables `a` and `b` that can hold decimal numbers.")
        self.assertEqual(explain.statement("total = add(x, y)", "python", k)[0],
                         "Calls `add()` and stores its result in `total` (Add two numbers).")
        self.assertEqual(explain.statement('name = input("Name? ")', "python", k)[0],
                         "Asks the user to type something and keeps the answer in `name`.")
        normal, simpler = explain.statement("run_stats();", "c", {})
        self.assertNotEqual(normal, simpler)

    def test_params(self):
        self.assertEqual(explain.param_names("(int numbers[], int count)", "c"), ["numbers", "count"])
        self.assertEqual(explain.param_names("(self, a, b=2)", "python"), ["a", "b"])
        self.assertEqual(explain.param_names("(a: number, b?: string)", "typescript"), ["a", "b?"])
        self.assertEqual(explain.param_names("(void)", "c"), [])


class ExplainerApiTest(unittest.TestCase):
    """The explainer endpoints, with the fake AI and with AI switched off."""

    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app, limiter
        from app import config
        from app.services.llm import llm
        config.AI_PROVIDER, config.AI_FALLBACK = "fake", "none"
        limiter.hits.clear()
        llm.reset()
        llm.sleep = lambda s: None
        self.client = TestClient(app)

    def project(self, sample: str) -> str:
        pid = self.client.post("/api/projects/sample", json={"sample": sample}).json()["id"]
        for _ in range(200):
            if self.client.get(f"/api/projects/{pid}").json()["status"] == "ready":
                return pid
            time.sleep(0.05)
        raise AssertionError("not ready")

    def test_file_and_function_views(self):
        pid = self.project("calculator-c")
        f = self.client.get(f"/api/projects/{pid}/code", params={"path": "stats.h"}).json()
        self.assertEqual([b["kind"] for b in f["blocks"]], ["about", "guard", "setup", "prototypes"])
        self.assertIn("`MAX_NUMBERS` = `10`", f["blocks"][2]["explain"])
        fn = self.client.get(f"/api/projects/{pid}/function", params={"id": "main.c::run_choice"}).json()
        self.assertTrue(fn["mermaid"].startswith("flowchart TD"))
        self.assertEqual(fn["blocks"][0]["kind"], "start")
        self.assertEqual(fn["blocks"][-1]["kind"], "end")
        self.assertTrue(all(b["explain"] and b["simpler"] for b in fn["blocks"]))
        self.assertEqual(self.client.get(f"/api/projects/{pid}/function", params={"id": "nope"}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/projects/{pid}/code", params={"path": "../x"}).status_code, 404)

    def test_ai_explanations_and_simpler(self):
        pid = self.project("todo-python")
        result = None
        for level in ("normal", "simpler"):
            for _ in range(100):
                result = self.client.get(f"/api/projects/{pid}/explain",
                                         params={"id": "todo.py::main", "level": level}).json()
                if result["status"] != "thinking":
                    break
                time.sleep(0.05)
            self.assertEqual(result["status"], "done")
            self.assertIn("n1", result["blocks"])
        self.assertTrue(result["blocks"]["n1"].startswith("Simply put"))

    def test_without_ai_the_explainer_still_works(self):
        from app import config
        from app.services.llm import llm
        old = config.AI_PROVIDER
        config.AI_PROVIDER, config.AI_FALLBACK = "gemini", "none"     # no key -> AI off
        llm.reset()
        try:
            pid = self.project("miniauth")
            fn = self.client.get(f"/api/projects/{pid}/function", params={"id": "auth.py::login"}).json()
            self.assertIn("user is None?", fn["mermaid"])
            for _ in range(100):
                r = self.client.get(f"/api/projects/{pid}/explain", params={"id": "auth.py::login", "level": "simpler"}).json()
                if r["status"] != "thinking":
                    break
                time.sleep(0.03)
            self.assertEqual(r["status"], "off")
            self.assertIn("switched off", r["message"])
        finally:
            config.AI_PROVIDER, config.AI_FALLBACK = old, "none"
            llm.reset()


if __name__ == "__main__":
    unittest.main()
