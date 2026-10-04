"""A small glossary of programming ideas, for beginners.

Used by the chat for concept questions ("what is a for loop used for?"):
first a short general explanation from here, then the example from the
student's own code (found by constructs.py). Also the source for hover
explanations of terms later on.

Each entry: the words that name it, the code constructs that show it, and a
normal and a simpler explanation. Kept short and language-neutral; the
example from the student's code does the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Term:
    key: str
    title: str
    patterns: tuple[str, ...]        # regexes, matched against the lower-case question
    kinds: tuple[str, ...]           # constructs.py kinds that are examples of it
    normal: str
    simpler: str


TERMS: list[Term] = [
    Term("for_loop", "for loop", (r"\bfor[- ]?loops?\b", r"\bfor statements?\b", r"`for`", r"\bfor each\b", r"\bforeach\b"),
         ("for_loop",),
         "A for loop repeats some steps a set number of times, or once for every item in a list. "
         "In C, Java and JavaScript it has three parts: a start (`i = 0`), a test (`i < n`) and a step "
         "(`i++`). It is used when you know how many times to repeat, for example to go through an array.",
         "A for loop says \"do this 10 times\" or \"do this for every item\", like checking every name on a list."),
    Term("while_loop", "while loop", (r"\bwhile[- ]?loops?\b", r"\bwhile statements?\b", r"`while`", r"\bwhile\b"),
         ("while_loop",),
         "A while loop repeats some steps as long as a condition stays true. It is used when you don't know "
         "in advance how many times to repeat, for example \"keep showing the menu until the user chooses quit\".",
         "A while loop says \"keep going until something changes\", like stirring soup until it boils."),
    Term("do_while", "do-while loop", (r"\bdo[- ]?while\b",), ("do_while",),
         "A do-while loop runs its steps once first and then repeats them while a condition is true, "
         "so the steps always run at least once.",
         "Do it once, then keep doing it while the rule says so."),
    Term("loop", "loop", (r"\bloops?\b", r"\brepeat(s|ing)?\b", r"\biterat(e|ion)\b"),
         ("for_loop", "while_loop", "do_while"),
         "A loop repeats some steps. A `for` loop is used when you know how many times; a `while` loop "
         "repeats until a condition changes.",
         "A loop does the same thing again and again, until it is told to stop."),
    Term("if", "if statement", (r"\bif[- ]?(else|statements?|conditions?|blocks?)\b", r"`if`", r"\belse\b",
                                r"\bconditions?\b", r"\bconditionals?\b"),
         ("if",),
         "An if statement makes a decision: it checks a condition, runs some steps only when the condition is "
         "true, and can run other steps (`else`) when it is false.",
         "An if is a question with two roads: yes goes one way, no goes the other."),
    Term("switch", "switch statement", (r"\bswitch\b", r"\bswitch[- ]?case\b", r"\bcase statements?\b", r"\bmatch statements?\b"),
         ("switch",),
         "A switch statement picks one of several paths based on a single value, like a menu: `case 1` runs one "
         "part, `case 2` another, and `default` runs when nothing matches. In C, `break` ends each case; "
         "without it the code runs on into the next case.",
         "A switch is like a menu: pick a number and you get that dish."),
    Term("return", "return", (r"\breturn(s|ing| statements?| values?)?\b",), ("return",),
         "`return` ends a function and gives a value back to the code that called it, like the answer of a "
         "calculation.",
         "`return` is the function handing back its answer and stopping."),
    Term("break", "break", (r"\bbreak\b",), ("break",),
         "`break` jumps out of a loop or a switch straight away.",
         "`break` means \"stop this loop now\"."),
    Term("continue", "continue", (r"\bcontinue\b",), ("continue",),
         "`continue` skips the rest of this round of a loop and goes on with the next round.",
         "`continue` means \"skip to the next turn\"."),
    Term("function", "function", (r"\bfunctions?\b", r"\bmethods?\b", r"\bprocedures?\b"), ("function",),
         "A function is a named block of code that does one job. You write it once and \"call\" it whenever you "
         "need that job done; it can receive inputs (parameters) and give back a result (`return`).",
         "A function is a recipe with a name: call its name and the steps run."),
    Term("parameter", "parameter", (r"\bparameters?\b", r"\barguments?\b", r"\bparams?\b"), ("function",),
         "Parameters are the inputs a function receives, listed in brackets after its name. The values you pass "
         "in when calling it are called arguments.",
         "Parameters are the ingredients you hand to a recipe."),
    Term("variable", "variable", (r"\bvariables?\b",), ("declaration",),
         "A variable is a named place in memory that holds a value, like `total` or `choice`. You can read it "
         "and change it. In C and Java you also say its type, like `int` for whole numbers.",
         "A variable is a labelled box that holds one value."),
    Term("array", "array", (r"\barrays?\b", r"\blists?\b"), ("array",),
         "An array (a list in Python) holds many values of the same kind under one name. You reach each value "
         "by its position, starting at 0: `numbers[0]` is the first one. Loops are often used to go through them.",
         "An array is a row of boxes with numbers on them, starting at 0."),
    Term("pointer", "pointer", (r"\bpointers?\b", r"\baddress(es)?\b"), ("pointer",),
         "A pointer holds the memory address of another value. `&x` gives the address of `x`, which is why "
         "`scanf` needs `&`: it must know where to store what the user types.",
         "A pointer is a note that says where something is kept, not the thing itself."),
    Term("struct", "struct", (r"\bstructs?\b", r"\bstructures?\b"), ("struct",),
         "A struct groups several values that belong together under one name, like a point with an `x` and a `y`.",
         "A struct is a box with several labelled compartments."),
    Term("header", "header file", (r"\bheader files?\b", r"\.h files?\b", r"#include", r"\binclude\b"), ("include",),
         "A header file (`.h`) lists the functions another file provides. `#include \"calc.h\"` lets one C file "
         "use functions written in another file.",
         "A header is a menu of what another file can do for you."),
    Term("print", "printf / print", (r"\bprintf\b", r"\bprint(ing|s)?\b", r"\boutput\b"), ("print",),
         "`printf` (in Python `print`, in Java `System.out.println`) shows text on the screen. In `printf`, codes "
         "like `%d` (whole number) and `%f` (decimal) are replaced by the values that follow.",
         "`printf` writes words on the screen."),
    Term("input", "scanf / input", (r"\bscanf\b", r"\binput\b", r"\buser types\b", r"\bread(ing)? input\b"), ("input",),
         "`scanf` (in Python `input`) waits for the user to type something and stores it in a variable. "
         "In C you pass the variable's address with `&`.",
         "`scanf` waits for you to type, then puts it in a box."),
    Term("recursion", "recursion", (r"\brecursi(on|ve)\b",), ("recursion",),
         "Recursion is when a function calls itself to solve a smaller piece of the same problem, with a stop "
         "rule so it doesn't go on forever.",
         "A recursive function asks a smaller copy of itself for help."),
]

# "what is a ...", "why use ...", "when should I use ..." - a question about an idea
CONCEPT_Q = re.compile(r"^\s*(what('?s| is| are| does)\b|why\b|when (do|should|would)\b|how (do|does|can) (i|you|we) use\b|"
                       r"explain\b|define\b|meaning of\b|what do(es)? .* mean\b)|\bused for\b|\bpurpose of\b|\bmean(s|ing)?\b",
                       re.I)


def find_terms(question: str) -> list[Term]:
    """Glossary terms named in the question, most specific first (for loop before loop)."""
    q = question.lower()
    hits = [t for t in TERMS if any(re.search(p, q) for p in t.patterns)]
    # "for loop" makes the plain "loop" entry redundant
    if any(t.key in ("for_loop", "while_loop", "do_while") for t in hits):
        hits = [t for t in hits if t.key != "loop"]
    # a bare "while" in "while the program runs" is not about loops unless asked about one
    if any(t.key == "while_loop" for t in hits) and not re.search(r"\bwhile[- ]?loops?\b|`while`|\bwhile statement", q) \
            and not re.search(r"\bloop", q):
        hits = [t for t in hits if t.key != "while_loop"]
    return hits


def is_concept_question(question: str, terms: Optional[list[Term]] = None) -> bool:
    """'What is a for loop used for?' yes; 'What does the loop in main() do?' no (that asks about
    a specific piece of code, which names a function or says 'in/this')."""
    terms = find_terms(question) if terms is None else terms
    if not terms or not CONCEPT_Q.search(question):
        return False
    specific = re.search(r"\b(in|inside) [\w.]+\(\)|\b(this|that) (loop|function|if|switch)|\bthe \w+ in\b|\w+\(\)", question, re.I)
    return not specific


def get(key: str) -> Optional[Term]:
    return next((t for t in TERMS if t.key == key), None)
