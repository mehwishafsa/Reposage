"""Flowcharts and step-by-step explanations for one function - no AI needed.

We read the function's syntax tree (Tree-sitter) and turn its statements
into boxes and arrows, the way a teacher draws a flowchart on the board:

    start (the function's name and inputs)
    action      plain statements, up to 3 per box     ["x = 1"]
    decision    if / else if / elif                   {"x > 0?"}  yes / no
    loop        for / while: test, body, back again   {{"i < n?"}} repeat / done
    switch      switch-case / match, with fall-through in C, JS and Java
    try         try / catch: an extra "if error" path
    return      gives a value back, goes to the end
    end

Every box remembers its code lines (so clicking it highlights the code) and
gets a plain-English explanation from explain.py, in a normal and a simpler
version. The same code always gives the same flowchart.

Works for Python, JavaScript, TypeScript, Java and C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Node, Parser

from .. import config  # noqa: F401  (puts the reposage engine on sys.path)
from reposage.parsers import parser_for

from . import explain

MAX_BOXES = 70            # bigger functions: deep parts are folded into one box
LABEL_CHARS = 44

FUNC_TYPES = {
    "function_definition", "function_declaration", "method_declaration", "constructor_declaration",
    "method_definition", "arrow_function", "function_expression", "function", "generator_function_declaration",
    "compact_constructor_declaration",
}
BLOCKS = {"block", "statement_block", "compound_statement"}
SKIP = {"comment", "line_comment", "block_comment", "empty_statement", "pass_statement_doc"}
NESTED_DEFS = {"function_definition", "class_definition", "function_declaration", "class_declaration",
               "method_declaration", "decorated_definition", "struct_specifier", "local_class_declaration"}


@dataclass
class Box:
    id: str
    kind: str                    # start | action | decision | loop | switch | try | return | stop | end
    label: str
    lines: list[int]             # [first, last] lines this box stands for
    scope: list[int]             # whole statement (e.g. the full if-block), for a lighter highlight
    explain: str = ""
    simpler: str = ""


@dataclass
class Ctx:
    """Where `break` and `continue` lead, inside loops and switches."""
    brk: Optional[list] = None
    cont: Optional[list] = None
    returns: list = field(default_factory=list)


class FlowBuilder:
    def __init__(self, lang: str, source: bytes, known: dict[str, str]):
        self.lang = lang
        self.src = source
        self.known = known            # function name -> one-line summary (from the project)
        self.boxes: list[Box] = []
        self.edges: list[tuple[str, str, str]] = []

    # ---- helpers --------------------------------------------------------
    def text(self, n: Optional[Node]) -> str:
        return n.text.decode("utf-8", "replace") if n is not None else ""

    def box(self, kind: str, label: str, n: Optional[Node], header: Optional[Node] = None,
            explain_pair: tuple[str, str] = ("", "")) -> str:
        bid = f"n{len(self.boxes)}"
        first = (n.start_point[0] + 1) if n is not None else 0
        last = ((header or n).end_point[0] + 1) if n is not None else 0
        scope = [first, n.end_point[0] + 1] if n is not None else [0, 0]
        self.boxes.append(Box(bid, kind, label, [first, last], scope, *explain_pair))
        return bid

    def link(self, loose: list, target: str) -> None:
        for src, label in loose:
            if (src, target, label) not in self.edges:
                self.edges.append((src, target, label))

    def full(self) -> bool:
        return len(self.boxes) >= MAX_BOXES

    # ---- the function ---------------------------------------------------
    def build(self, fn: Node, name: str) -> None:
        params = fn.child_by_field_name("parameters")
        if params is None:
            decl = fn.child_by_field_name("declarator")           # C: int add(int a, int b)
            while decl is not None and decl.type != "function_declarator":
                decl = decl.child_by_field_name("declarator")
            params = decl.child_by_field_name("parameters") if decl is not None else None
        names = explain.param_names(self.text(params), self.lang)
        header_end = fn.child_by_field_name("body") or fn
        start = self.box("start", f"start: {name}({', '.join(names)})", fn,
                         explain_pair=explain.start(name, names))
        self.boxes[-1].lines = [fn.start_point[0] + 1, max(fn.start_point[0] + 1, header_end.start_point[0] + 1)]
        ctx = Ctx()
        body = fn.child_by_field_name("body")
        loose = [(start, "")]
        if body is None:
            pass
        elif body.type in BLOCKS:
            loose = self.seq(self.children(body), loose, ctx)
        else:                                                   # arrow function: x => x + 1
            rid = self.box("return", "return " + self.short(body), body,
                           explain_pair=explain.returns(self.text(body)))
            self.link(loose, rid)
            loose, ctx.returns = [], [(rid, "")]
        end = self.box("end", "end", None, explain_pair=explain.end(name))
        self.boxes[-1].lines = [fn.end_point[0] + 1, fn.end_point[0] + 1]
        self.link(loose + ctx.returns, end)

    def children(self, block: Node) -> list[Node]:
        kids = [c for c in block.named_children if c.type not in SKIP]
        # A docstring at the top of a Python function is a description, not a step.
        if self.lang == "python" and kids and kids[0].type == "expression_statement" \
                and kids[0].named_children and kids[0].named_children[0].type == "string":
            kids = kids[1:]
        return kids

    # ---- statements -----------------------------------------------------
    def seq(self, stmts: list[Node], loose: list, ctx: Ctx) -> list:
        i = 0
        while i < len(stmts):
            if not loose:
                break                                     # code after return/break is never reached
            s = stmts[i]
            if self.is_simple(s):
                group = [s]
                while i + 1 < len(stmts) and len(group) < 3 and self.is_simple(stmts[i + 1]):
                    i += 1
                    group.append(stmts[i])
                loose = self.action(group, loose)
            else:
                loose = self.stmt(s, loose, ctx)
            i += 1
        return loose

    def is_simple(self, s: Node) -> bool:
        return s.type not in COMPOUND and s.type not in JUMPS and s.type not in BLOCKS

    def action(self, group: list[Node], loose: list) -> list:
        texts = [self.text(s) for s in group]
        label = "\n".join(self.short(s) for s in group)
        bid = self.box("action", label, group[0], explain_pair=explain.actions(texts, self.lang, self.known))
        self.boxes[-1].lines = [group[0].start_point[0] + 1, group[-1].end_point[0] + 1]
        self.boxes[-1].scope = list(self.boxes[-1].lines)
        self.link(loose, bid)
        return [(bid, "")]

    def stmt(self, s: Node, loose: list, ctx: Ctx) -> list:
        t = s.type
        if t in BLOCKS:
            return self.seq(self.children(s), loose, ctx)
        if t in JUMPS:
            return self.jump(s, loose, ctx)
        if self.full() or t in NESTED_DEFS:
            return self.action([s], loose)
        handler = getattr(self, "do_" + COMPOUND.get(t, ""), None)
        return handler(s, loose, ctx) if handler else self.action([s], loose)

    def body_of(self, n: Optional[Node]) -> list[Node]:
        """Statements inside a clause (a block, or a single statement)."""
        if n is None:
            return []
        if n.type in BLOCKS:
            return self.children(n)
        if n.type in ("else_clause", "elif_clause", "finally_clause", "except_clause", "catch_clause",
                      "case_clause", "else"):
            inner = n.child_by_field_name("body") or n.child_by_field_name("consequence")
            if inner is not None:
                return self.body_of(inner)
            kids = [c for c in n.named_children if c.type not in SKIP and c.type not in
                    ("as_pattern", "catch_formal_parameter", "identifier", "case_pattern")]
            return self.body_of(kids[-1]) if kids else []
        return [n]

    # -- jumps
    def jump(self, s: Node, loose: list, ctx: Ctx) -> list:
        t = s.type
        if t == "break_statement" and ctx.brk is not None:
            ctx.brk.extend(loose)
            return []
        if t == "continue_statement" and ctx.cont is not None:
            ctx.cont.extend(loose)
            return []
        if t == "return_statement":
            rid = self.box("return", self.short(s), s, explain_pair=explain.returns(self.text(s)))
            self.link(loose, rid)
            ctx.returns.append((rid, ""))
            return []
        if t in ("raise_statement", "throw_statement"):
            rid = self.box("stop", self.short(s), s, explain_pair=explain.raises(self.text(s)))
            self.link(loose, rid)
            ctx.returns.append((rid, "error"))
            return []
        return self.action([s], loose)

    # -- if / else if / elif / else
    def do_if(self, s: Node, loose: list, ctx: Ctx) -> list:
        cond = s.child_by_field_name("condition")
        cons = s.child_by_field_name("consequence")
        alts = [s.children[i] for i in range(s.child_count) if s.field_name_for_child(i) == "alternative"]
        return self.decision(s, cond, cons, alts, loose, ctx)

    def decision(self, s: Node, cond: Node, cons: Node, alts: list[Node], loose: list, ctx: Ctx) -> list:
        ctext = explain.strip_parens(self.text(cond))
        did = self.box("decision", self.short_text(ctext) + "?", s, header=cond,
                       explain_pair=explain.decision(ctext, self.lang))
        self.link(loose, did)
        out = self.seq(self.body_of(cons), [(did, "yes")], ctx)
        if not alts:
            return out + [(did, "no")]
        alt = alts[0]
        if alt.type == "elif_clause":
            return out + self.decision(alt, alt.child_by_field_name("condition"),
                                       alt.child_by_field_name("consequence"), alts[1:], [(did, "no")], ctx)
        stmts = self.body_of(alt)
        return out + self.seq(stmts, [(did, "no")], ctx) if stmts else out + [(did, "no")]

    # -- loops
    def do_while(self, s: Node, loose: list, ctx: Ctx) -> list:
        cond = s.child_by_field_name("condition")
        ctext = explain.strip_parens(self.text(cond))
        forever = ctext in ("True", "true", "1")            # while True: only break/return ends it
        hid = self.box("loop", "repeat forever" if forever else self.short_text(ctext) + "?", s, header=cond,
                       explain_pair=explain.for_loop("", "", "", self.lang) if forever
                       else explain.while_loop(ctext, self.lang))
        self.link(loose, hid)
        inner = Ctx(brk=[], cont=[], returns=ctx.returns)
        out = self.seq(self.body_of(s.child_by_field_name("body")), [(hid, "" if forever else "yes")], inner)
        self.link(out + inner.cont, hid)
        return ([] if forever else [(hid, "no")]) + inner.brk

    def do_for(self, s: Node, loose: list, ctx: Ctx) -> list:
        """C-style for: init -> test -> body -> update -> test again."""
        if s.child_by_field_name("left") is not None:          # Python: for x in items
            return self.do_foreach(s, loose, ctx)
        init =s.child_by_field_name("initializer") or s.child_by_field_name("init")
        cond = s.child_by_field_name("condition")
        upd = s.child_by_field_name("update") or s.child_by_field_name("increment")
        if init is not None:
            iid = self.box("action", self.short(init).rstrip(";"), init,
                           explain_pair=explain.actions([self.text(init)], self.lang, self.known))
            self.link(loose, iid)
            loose = [(iid, "")]
        ctext = explain.strip_parens(self.text(cond)) if cond is not None else ""
        hid = self.box("loop", (self.short_text(ctext) + "?") if ctext else "repeat forever", s,
                       header=s.child_by_field_name("body") and _header_end(s),
                       explain_pair=explain.for_loop(self.text(init), ctext, self.text(upd), self.lang))
        self.link(loose, hid)
        inner = Ctx(brk=[], cont=[], returns=ctx.returns)
        out = self.seq(self.body_of(s.child_by_field_name("body")), [(hid, "yes")], inner)
        back = out + inner.cont
        if upd is not None:
            uid = self.box("action", self.short(upd), upd,
                           explain_pair=explain.actions([self.text(upd)], self.lang, self.known))
            self.link(back, uid)
            back = [(uid, "")]
        self.link(back, hid)
        return ([(hid, "no")] if ctext else []) + inner.brk

    def do_foreach(self, s: Node, loose: list, ctx: Ctx) -> list:
        """for x in items / for (x of items) / for (T x : items)."""
        var = s.child_by_field_name("left") or s.child_by_field_name("name")
        items = s.child_by_field_name("right") or s.child_by_field_name("value")
        v, it = self.text(var), self.text(items)
        hid = self.box("loop", self.short_text(f"next {v} in {it}") + "?", s, header=_header_end(s),
                       explain_pair=explain.foreach(v, it))
        self.link(loose, hid)
        inner = Ctx(brk=[], cont=[], returns=ctx.returns)
        out = self.seq(self.body_of(s.child_by_field_name("body")), [(hid, "yes")], inner)
        self.link(out + inner.cont, hid)
        return [(hid, "no more")] + inner.brk

    def do_dowhile(self, s: Node, loose: list, ctx: Ctx) -> list:
        """do { body } while (test): the body always runs at least once."""
        first = len(self.boxes)
        inner = Ctx(brk=[], cont=[], returns=ctx.returns)
        out = self.seq(self.body_of(s.child_by_field_name("body")), loose, inner)
        cond = s.child_by_field_name("condition")
        ctext = explain.strip_parens(self.text(cond))
        cid = self.box("loop", self.short_text(ctext) + "?", cond, explain_pair=explain.do_while(ctext, self.lang))
        self.link(out + inner.cont, cid)
        entry = self.boxes[first].id if first < len(self.boxes) - 1 else cid
        if entry == cid:
            self.link(loose, cid)
        self.edges.append((cid, entry, "yes"))
        return [(cid, "no")] + inner.brk

    # -- switch / match
    def do_switch(self, s: Node, loose: list, ctx: Ctx) -> list:
        value = s.child_by_field_name("value") or s.child_by_field_name("condition") \
            or s.child_by_field_name("subject")
        vtext = explain.strip_parens(self.text(value))
        body = s.child_by_field_name("body")
        cases = self.switch_cases(body)
        sid = self.box("switch", self.short_text(f"which {vtext}?"), s, header=value,
                       explain_pair=explain.switch(vtext, [c[0] for c in cases]))
        self.link(loose, sid)
        inner = Ctx(brk=[], cont=ctx.cont, returns=ctx.returns)
        exits, fall, waiting, has_default = [], [], [], False
        for labels, stmts, falls_through in cases:
            has_default |= "default" in labels or "_" in labels
            waiting += labels
            if not stmts and falls_through:
                continue                         # "case 2: case 3:" share the next statements
            incoming = [(sid, ", ".join(waiting))] + fall
            waiting = []
            out = self.seq(stmts, incoming, inner) if stmts else incoming
            if falls_through:
                fall = out                       # no break: runs into the next case (C, JS, Java)
            else:
                exits += out
                fall = []
        if waiting:
            exits.append((sid, ", ".join(waiting)))
        exits += fall + inner.brk
        if not has_default:
            exits.append((sid, "other"))
        return exits

    def switch_cases(self, body: Optional[Node]) -> list[tuple[list[str], list[Node], bool]]:
        """[(labels, statements, falls_through)] in source order."""
        out = []
        if body is None:
            return out
        for c in body.named_children:
            if c.type in ("switch_case", "switch_default"):                       # JS / TS
                value = c.child_by_field_name("value")
                stmts = [c.children[i] for i in range(c.child_count) if c.field_name_for_child(i) == "body"]
                out.append(([self.text(value)] if value is not None else ["default"],
                            [x for x in stmts if x.type not in SKIP], True))
            elif c.type == "case_statement":                                       # C
                value = c.child_by_field_name("value")
                stmts = [x for x in c.named_children if not (value is not None and x == value) and x.type not in SKIP]
                out.append(([self.text(value)] if value is not None else ["default"], stmts, True))
            elif c.type in ("switch_block_statement_group", "switch_rule"):         # Java
                labels = [self.text(l).replace("case", "", 1).strip() or "default"
                          for l in c.named_children if l.type == "switch_label"]
                stmts = [x for x in c.named_children if x.type not in ("switch_label",) and x.type not in SKIP]
                out.append((labels, stmts, c.type == "switch_block_statement_group"))
            elif c.type == "case_clause":                                          # Python match
                pattern = next((x for x in c.named_children if x.type == "case_pattern"), None)
                out.append(([self.text(pattern) or "_"], self.body_of(c.child_by_field_name("consequence")), False))
        return out

    # -- try / except / catch / finally
    def do_try(self, s: Node, loose: list, ctx: Ctx) -> list:
        tid = self.box("try", "try", s, header=s.children[0], explain_pair=explain.try_block())
        self.link(loose, tid)
        out = self.seq(self.body_of(s.child_by_field_name("body")), [(tid, "")], ctx)
        final = None
        for c in s.named_children:
            if c.type in ("except_clause", "catch_clause"):
                out += self.seq(self.body_of(c), [(tid, "if error")], ctx)
            elif c.type == "else_clause":
                pass
            elif c.type == "finally_clause":
                final = c
        if final is not None:
            out = self.seq(self.body_of(final), out, ctx) if out else out
        return out

    def do_with(self, s: Node, loose: list, ctx: Ctx) -> list:
        clause = next((c for c in s.named_children if c.type == "with_clause"), None)
        wid = self.box("action", "with " + self.short(clause), s, header=clause,
                       explain_pair=explain.with_block(self.text(clause)))
        self.link(loose, wid)
        return self.seq(self.body_of(s.child_by_field_name("body")), [(wid, "")], ctx)

    # ---- labels ---------------------------------------------------------
    def short(self, n: Optional[Node]) -> str:
        return self.short_text(self.text(n))

    @staticmethod
    def short_text(text: str) -> str:
        line = " ".join(text.split())
        return line if len(line) <= LABEL_CHARS else line[: LABEL_CHARS - 1] + "…"

    # ---- output ---------------------------------------------------------
    def mermaid(self) -> str:
        shapes = {"start": ('(["', '"])'), "end": ('(["', '"])'), "action": ('["', '"]'),
                  "decision": ('{"', '"}'), "switch": ('{"', '"}'), "loop": ('{{"', '"}}'),
                  "try": ('[/"', '"/]'), "return": ('(["', '"])'), "stop": ('(["', '"])')}
        out = ["flowchart TD"]
        for b in self.boxes:
            left, right = shapes[b.kind]
            out.append(f"  {b.id}{left}{_mm(b.label)}{right}")
        for src, dst, label in self.edges:
            arrow = f' -->|"{_mm(label)}"| ' if label else " --> "
            out.append(f"  {src}{arrow}{dst}")
        for kind in ("start", "end", "action", "decision", "switch", "loop", "try", "return", "stop"):
            ids = [b.id for b in self.boxes if b.kind == kind]
            if ids:
                out.append(f"  class {','.join(ids)} k_{kind}")
        return "\n".join(out)


COMPOUND = {
    "if_statement": "if",
    "while_statement": "while",
    "for_statement": "for",
    "for_in_statement": "foreach",            # JS for..in / for..of
    "enhanced_for_statement": "foreach",      # Java for (T x : xs)
    "do_statement": "dowhile",
    "switch_statement": "switch",
    "switch_expression": "switch",
    "match_statement": "switch",
    "try_statement": "try",
    "try_with_resources_statement": "try",
    "with_statement": "with",
}
JUMPS = {"return_statement", "break_statement", "continue_statement", "raise_statement", "throw_statement"}


def _header_end(s: Node) -> Optional[Node]:
    """The part of a loop before its body, e.g. `for (i = 0; i < n; i++)`."""
    body = s.child_by_field_name("body")
    if body is None:
        return None
    prev = body.prev_sibling
    return prev if prev is not None else s


def _mm(text: str) -> str:
    """Make text safe inside a quoted Mermaid label."""
    text = (text.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;")
            .replace(">", "#gt;").replace("\n", "<br/>"))
    return text.replace("#lt;br/#gt;", "<br/>") if "<br/>" in text else text


# ---------------------------------------------------------------------------

def find_function(root: Node, start_line: int, end_line: int) -> Optional[Node]:
    """The syntax node of the function the graph found at these lines."""
    best = None
    stack = [root]
    while stack:
        n = stack.pop()
        if n.start_point[0] > end_line - 1 or n.end_point[0] < start_line - 1:
            continue
        if n.type in FUNC_TYPES and n.end_point[0] == end_line - 1 and \
                start_line - 1 <= n.start_point[0] <= start_line + 4:
            if best is None or n.start_point[0] < best.start_point[0]:
                best = n
        stack.extend(n.named_children)
    return best


def build_flowchart(path: str, source: bytes, start_line: int, end_line: int, name: str,
                    known: dict[str, str]) -> Optional[dict]:
    parser = parser_for(path)
    if parser is None:
        return None
    tree = Parser(parser.ts_language(path)).parse(source)
    fn = find_function(tree.root_node, start_line, end_line)
    if fn is None:
        return None
    builder = FlowBuilder(parser.name, source, known)
    builder.build(fn, name)
    return {"mermaid": builder.mermaid(),
            "boxes": [b.__dict__ for b in builder.boxes],
            "edges": [{"from": a, "to": b, "label": l} for a, b, l in builder.edges],
            "folded": len(builder.boxes) >= MAX_BOXES}
