"""Plain-English explanations of code, written from patterns - no AI.

Every function returns two versions:  (normal, simpler)
    normal   short, clear English for a first-year student
    simpler  even shorter, with everyday words ("a box called total")
Code is wrapped in `backticks`; the website shows it as code.

These are used when the AI is off or busy, and as the starting point the
AI improves on. They are deliberately careful: when we can't tell what a
line does, we say what it LOOKS like instead of guessing.
"""

from __future__ import annotations

import re

Pair = tuple[str, str]

TYPE_WORDS = {
    "int": "whole numbers", "long": "whole numbers", "short": "whole numbers", "unsigned": "whole numbers",
    "float": "decimal numbers", "double": "decimal numbers", "char": "a character", "bool": "true/false",
    "boolean": "true/false", "String": "text", "string": "text",
}
PRINT_FUNCS = {"printf", "puts", "putchar", "print", "println", "console.log", "System.out.println",
               "System.out.print", "System.out.printf", "fprintf", "console.error", "alert"}
INPUT_FUNCS = {"scanf", "input", "gets", "fgets", "getchar", "prompt", "readline", "nextInt", "nextLine",
               "nextDouble", "next"}


# ---------------------------------------------------------------------------
# Conditions:  `b == 0 && x > 1`  ->  "`b` is equal to 0 and `x` is more than 1"
# ---------------------------------------------------------------------------

_OPS = [
    (r"\s*===\s*", " is equal to "), (r"\s*!==\s*", " is not equal to "),
    (r"\s*==\s*", " is equal to "), (r"\s*!=\s*", " is not equal to "),
    (r"\s*>=\s*", " is at least "), (r"\s*<=\s*", " is at most "),
    (r"\s*>\s*", " is more than "), (r"\s*<\s*", " is less than "),
    (r"\s*&&\s*", " and "), (r"\s*\|\|\s*", " or "),
    (r"\bis not None\b", " has a value"), (r"\bis None\b", " has no value (is None)"),
    (r"\bnot in\b", " is not one of "), (r"\bnot\s+", "not "), (r"!(?!=)", "not "),
]


def condition_words(cond: str) -> str:
    """Turn a condition into words, keeping names and values as code."""
    cond = strip_parens(" ".join(cond.split()))
    if len(cond) > 90 or not cond:
        return f"`{cond}`"
    if cond == "True" or cond == "true" or cond == "1":
        return "always true"
    parts = re.split(r"(\s*===\s*|\s*!==\s*|\s*==\s*|\s*!=\s*|\s*>=\s*|\s*<=\s*|(?<![-=])\s*>\s*|\s*<\s*|"
                     r"\s*&&\s*|\s*\|\|\s*|\s+and\s+|\s+or\s+|\bis not None\b|\bis None\b|\bnot in\b|^not\s+|^!)",
                     cond)
    out = []
    for p in parts:
        if not p:
            continue
        key = p.strip()
        word = {"===": "is equal to", "==": "is equal to", "!==": "is not equal to", "!=": "is not equal to",
                ">=": "is at least", "<=": "is at most", ">": "is more than", "<": "is less than",
                "&&": "and", "||": "or", "and": "and", "or": "or", "is not None": "has a value",
                "is None": "has no value (None)", "not in": "is not in", "not": "not", "!": "not"}.get(key)
        out.append(word if word else f"`{key}`")
    return " ".join(out)


def strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            return False
    return depth == 0


# ---------------------------------------------------------------------------
# Boxes of the flowchart
# ---------------------------------------------------------------------------

def param_names(params: str, lang: str) -> list[str]:
    inner = strip_parens(params)
    if not inner or inner == "void":
        return []
    names = []
    for part in _split_top(inner):
        part = part.split("=")[0].strip()          # default values
        part = re.sub(r"[\[\]*&]", " ", part).split(":")[0].strip()   # C arrays/pointers, TS types
        words = part.split()
        if words:
            names.append(words[-1] if lang in ("c", "java") else words[0].lstrip("."))
    return [n for n in names if n not in ("self", "cls")]


def start(name: str, params: list[str]) -> Pair:
    if params:
        given = _and([f"`{p}`" for p in params])
        return (f"The function `{name}` starts here. It receives {given} from the code that calls it.",
                f"`{name}` begins. It is handed {given}.")
    return (f"The function `{name}` starts here. It doesn't need any inputs.",
            f"`{name}` begins. It needs nothing to start.")


def end(name: str) -> Pair:
    return (f"`{name}` is finished, and the program goes back to the code that called it.",
            "Done! Back to whoever called this function.")


def decision(cond: str, lang: str) -> Pair:
    words = condition_words(cond)
    return (f"A decision: the code checks whether {words}. If yes, it follows the \"yes\" arrow; "
            "if not, the \"no\" arrow.",
            f"Check: {words}? Yes goes one way, no goes the other.")


def while_loop(cond: str, lang: str) -> Pair:
    words = condition_words(cond)
    return (f"A loop: as long as {words}, the steps inside run again and again. "
            "When it stops being true, the loop ends.",
            f"Repeat while {words}. Stop when it isn't.")


def do_while(cond: str, lang: str) -> Pair:
    words = condition_words(cond)
    return (f"After running the steps once, the code checks whether {words}. If yes, it does them again.",
            f"Do it once, then repeat while {words}.")


def for_loop(init: str, cond: str, update: str, lang: str) -> Pair:
    init, update = _clean(init).rstrip(";"), _clean(update)
    if not cond:
        return ("A loop with no stop test: it repeats until a `break` or `return` inside it.",
                "Repeat forever (until something inside says stop).")
    words = condition_words(cond)
    step = f", and after each round `{update}` runs" if update else ""
    first = f"It starts with `{init}`. " if init else ""
    return (f"A counting loop. {first}Before every round it checks whether {words}. "
            f"If yes, it runs the steps inside{step}. If no, the loop ends.",
            f"Count with a loop: keep going while {words}.")


def foreach(var: str, items: str) -> Pair:
    return (f"A loop that takes each item from `{items}` one by one, calls it `{var}`, "
            "and runs the steps inside for it. It stops when there are no items left.",
            f"For every `{var}` in `{items}`, do the steps inside.")


def switch(value: str, labels: list[list[str]]) -> Pair:
    flat = [l for group in labels for l in group]
    shown = ", ".join(f"`{l}`" for l in flat[:6] if l not in ("default", "_"))
    other = " If none match, it takes the `default` path." if any(l in ("default", "_") for l in flat) else \
        " If none match, it skips all of them."
    return (f"A switch: it looks at the value of `{value}` and jumps to the matching case ({shown}).{other}",
            f"Pick a path based on `{value}`, like choosing from a menu.")


def try_block() -> Pair:
    return ("Try the steps below. If something goes wrong (an error), jump to the error-handling part "
            "instead of crashing.",
            "Try it; if it breaks, use the backup plan.")


def with_block(clause: str) -> Pair:
    clause = _clean(clause)
    return (f"Opens `{clause}` for the steps inside, and closes it again automatically afterwards.",
            "Open something, use it, and it gets closed for you.")


def returns(stmt: str) -> Pair:
    value = re.sub(r"^return\b", "", _clean(stmt)).strip().rstrip(";").strip()
    if not value:
        return ("Stops the function here and goes back to the code that called it.",
                "Stop here and go back.")
    return (f"Gives back `{_cut(value)}` as the answer, and the function stops here.",
            f"Hand back `{_cut(value)}` and stop.")


def raises(stmt: str) -> Pair:
    return (f"Stops with an error: `{_cut(_clean(stmt))}`. The caller has to deal with it.",
            "Stop and shout \"error!\".")


# ---------------------------------------------------------------------------
# Plain statements
# ---------------------------------------------------------------------------

def actions(stmts: list[str], lang: str, known: dict[str, str]) -> Pair:
    pairs = [statement(s, lang, known) for s in stmts]
    if len(pairs) == 1:
        return pairs[0]
    normal = " Then ".join(_lower_first(p[0], i) for i, p in enumerate(pairs))
    simpler = " Then ".join(_lower_first(p[1], i) for i, p in enumerate(pairs))
    return normal, simpler


def statement(stmt: str, lang: str, known: dict[str, str]) -> Pair:
    s = _clean(stmt).rstrip(";").strip()
    if s in ("pass", ""):
        return ("Does nothing (a placeholder).", "Nothing happens here.")

    # x++ / x += 2 / i--
    m = re.fullmatch(r"(\+\+|--)?([\w.\[\]]+)(\+\+|--)?", s)
    if m and (m.group(1) or m.group(3)):
        name, op = m.group(2), m.group(1) or m.group(3)
        return ((f"Adds 1 to `{name}`." if op == "++" else f"Takes 1 away from `{name}`."),
                (f"`{name}` goes up by one." if op == "++" else f"`{name}` goes down by one."))
    m = re.fullmatch(r"([\w.\[\]]+)\s*([+\-*/%])=\s*(.+)", s)
    if m:
        name, op, val = m.groups()
        verb = {"+": f"Adds `{_cut(val)}` to", "-": f"Takes `{_cut(val)}` away from",
                "*": f"Multiplies", "/": f"Divides", "%": "Keeps the remainder of"}[op]
        tail = {"*": f" by `{_cut(val)}`", "/": f" by `{_cut(val)}`", "%": f" divided by `{_cut(val)}`"}.get(op, "")
        return (f"{verb} `{name}`{tail}.", f"Change the number in `{name}`.")

    # declarations without a value:  int i;   double a, b;
    m = re.fullmatch(r"(?:const\s+|static\s+|unsigned\s+)*(int|long|short|float|double|char|bool|boolean|String)"
                     r"\s+([\w\s,\[\]*]+)", s)
    if m and "=" not in s and "(" not in s:
        names = [re.sub(r"[\[\]*\d]", "", n).strip() for n in m.group(2).split(",")]
        kind = TYPE_WORDS.get(m.group(1), "values")
        boxes = _and([f"`{n}`" for n in names if n])
        word = "a variable" if len(names) == 1 else "variables"
        return (f"Makes {word} {boxes} that can hold {kind}.",
                f"Make empty boxes called {boxes}.")

    # assignment / declaration with a value:  x = ...   int x = ...   let x = ...
    m = re.fullmatch(r"(?:(?:const|let|var|final|static|unsigned|auto)\s+)*(?:([A-Za-z_][\w<>\[\]]*)\s+)?"
                     r"([A-Za-z_][\w.\[\]]*)\s*(?::\s*[\w<>\[\]|]+\s*)?=\s*(.+)", s)
    if m and not re.match(r"^\s*(if|while|for|return)\b", s):
        typ, name, value = m.groups()
        value = value.strip()
        made = typ and typ not in ("return",)
        call = _call_name(value)
        if call and _base_name(call) in INPUT_FUNCS:
            return (f"Asks the user to type something and keeps the answer in `{name}`.",
                    f"Wait for the user to type, then put it in the box `{name}`.")
        if call:
            what = _known(call, known)
            lead = f"Makes a variable `{name}` and stores" if made else "Stores"
            return (f"{lead} the result of `{_cut(value)}` in it{what}." if made else
                    f"Calls `{call}()` and stores its result in `{name}`{what}.",
                    f"Ask `{call}` for an answer and keep it in the box `{name}`.")
        if made:
            return (f"Makes a variable `{name}` and puts `{_cut(value)}` in it.",
                    f"New box `{name}`, holding `{_cut(value)}`.")
        return (f"Puts `{_cut(value)}` into `{name}`.", f"The box `{name}` now holds `{_cut(value)}`.")

    # a call on its own:  printf(...)  print(...)  add(a, b)
    call = _call_name(s)
    if call:
        base = _base_name(call)
        args = s[s.find("(") + 1: s.rfind(")")] if "(" in s else ""
        if call in PRINT_FUNCS or base in ("printf", "puts", "print", "println"):
            text = _first_string(args)
            shown = f": {text}" if text else ""
            inner = [c for c in re.findall(r"([A-Za-z_][\w.]*\s*\([^()]*\))", args)]
            if inner:
                shown += f", with the result of `{_cut(inner[0])}`"
            return (f"Shows a message on the screen{shown}.", f"Write on the screen{shown}.")
        if base in INPUT_FUNCS:
            targets = re.findall(r"&\s*([\w\[\]]+)", args)
            boxes = _and([f'`{t}`' for t in targets])
            return ((f"Waits for the user to type something and stores it in {boxes}." if targets else
                     "Waits for the user to type something."),
                    f"Wait for the user to type{f', then put it in {boxes}' if targets else ''}.")
        what = _known(call, known)
        return (f"Calls `{call}()` to do its job{what}.", f"Ask the helper `{call}` to do its job.")

    return (f"Runs `{_cut(s)}`.", f"Runs `{_cut(s)}`.")


# ---------------------------------------------------------------------------

def _known(call: str, known: dict[str, str]) -> str:
    summary = known.get(_base_name(call))
    return f" ({summary.rstrip('.')})" if summary else ""


def _call_name(s: str) -> str:
    m = re.match(r"^(?:await\s+|new\s+)?([A-Za-z_][\w.]*)\s*\(", s)
    return m.group(1) if m and s.rstrip().endswith(")") else ""


def _base_name(call: str) -> str:
    return call.rsplit(".", 1)[-1]


def _first_string(args: str) -> str:
    m = re.search(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', args)
    if not m:
        return ""
    text = (m.group(1) if m.group(1) is not None else m.group(2)).replace("\\n", " ").strip()
    text = re.sub(r"%[-\d.]*l?[dfsclu]", "…", text)          # printf placeholders
    text = re.sub(r"\{[^}]*\}", "…", text)                   # f-string parts
    return f"“{text}”" if text else ""


def _split_top(s: str) -> list[str]:
    """Split on commas that are not inside brackets."""
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _cut(text: str, limit: int = 50) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _lower_first(text: str, i: int) -> str:
    return text if i == 0 else text[:1].lower() + text[1:]
