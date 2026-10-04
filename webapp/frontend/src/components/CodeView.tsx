import { useEffect, useMemo, useRef } from "react";

const KEYWORDS: Record<string, string[]> = {
  python: ["def", "class", "return", "if", "elif", "else", "for", "while", "in", "not", "and", "or", "import", "from",
           "as", "try", "except", "finally", "with", "pass", "break", "continue", "None", "True", "False", "lambda",
           "raise", "is", "global", "yield", "match", "case", "async", "await"],
  c: ["int", "double", "float", "char", "void", "long", "short", "unsigned", "const", "static", "struct", "typedef",
      "return", "if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "sizeof",
      "enum", "#include", "#define", "#ifndef", "#endif", "#if", "NULL"],
  java: ["public", "private", "protected", "class", "interface", "static", "final", "void", "int", "double", "boolean",
         "char", "long", "new", "return", "if", "else", "for", "while", "do", "switch", "case", "default", "break",
         "continue", "try", "catch", "finally", "throw", "throws", "import", "package", "this", "null", "true", "false",
         "extends", "implements", "String"],
  javascript: ["function", "const", "let", "var", "return", "if", "else", "for", "while", "do", "switch", "case",
               "default", "break", "continue", "new", "class", "extends", "import", "from", "export", "try", "catch",
               "finally", "throw", "async", "await", "of", "in", "this", "null", "undefined", "true", "false", "typeof"],
};
KEYWORDS.typescript = [...KEYWORDS.javascript, "interface", "type", "enum", "implements", "public", "private",
                       "readonly", "string", "number", "boolean"];

type Tok = { t: string; c?: "kw" | "str" | "com" | "num" | "fn" };

/** Tiny highlighter: keywords, strings, comments, numbers, function names. */
function tokenize(lines: string[], lang: string): Tok[][] {
  const kw = new Set(KEYWORDS[lang] ?? KEYWORDS.javascript);
  const lineComment = lang === "python" ? "#" : "//";
  let inBlock = false;                                      // inside /* ... */ or """ ... """
  const blockEnd = lang === "python" ? '"""' : "*/";
  return lines.map((line) => {
    const out: Tok[] = [];
    let i = 0;
    const push = (t: string, c?: Tok["c"]) => { if (t) out.push({ t, c }); };
    while (i < line.length) {
      if (inBlock) {
        const end = line.indexOf(blockEnd, i);
        if (end === -1) { push(line.slice(i), "com"); i = line.length; break; }
        push(line.slice(i, end + blockEnd.length), "com"); i = end + blockEnd.length; inBlock = false; continue;
      }
      const rest = line.slice(i);
      if (lang !== "python" && rest.startsWith("/*")) { inBlock = true; continue; }
      if (lang === "python" && (rest.startsWith('"""') || rest.startsWith("'''"))) {
        const q = rest.slice(0, 3);
        const end = rest.indexOf(q, 3);
        if (end === -1) { push(rest, "com"); inBlock = q === '"""'; i = line.length; break; }
        push(rest.slice(0, end + 3), "com"); i += end + 3; continue;
      }
      if (rest.startsWith(lineComment) && !(lang === "c" && rest.startsWith("#include"))) { push(rest, "com"); break; }
      const ch = rest[0];
      if (ch === '"' || ch === "'" || ch === "`") {
        let j = 1;
        while (j < rest.length && rest[j] !== ch) j += rest[j] === "\\" ? 2 : 1;
        push(rest.slice(0, j + 1), "str"); i += j + 1; continue;
      }
      const word = /^#?[A-Za-z_][\w]*/.exec(rest);
      if (word) {
        const w = word[0];
        const isCall = rest.slice(w.length).trimStart().startsWith("(");
        push(w, kw.has(w) ? "kw" : isCall ? "fn" : undefined); i += w.length; continue;
      }
      const num = /^\d[\d.]*/.exec(rest);
      if (num) { push(num[0], "num"); i += num[0].length; continue; }
      push(ch); i += 1;
    }
    return out;
  });
}

const COLORS: Record<string, string> = {
  kw: "var(--c-c)", str: "var(--grass)", com: "var(--muted)", num: "var(--danger)", fn: "var(--link)",
};

export default function CodeView({ text, language, strong, scope, firstLine = 1, lastLine, onLine }: {
  text: string; language: string; strong: [number, number] | null; scope: [number, number] | null;
  firstLine?: number; lastLine?: number; onLine?: (line: number) => void;
}) {
  const all = useMemo(() => text.replace(/\r\n/g, "\n").split("\n"), [text]);
  const tokens = useMemo(() => tokenize(all, language), [all, language]);
  const box = useRef<HTMLDivElement>(null);
  const last = Math.min(lastLine ?? all.length, all.length);

  // keep the highlighted lines in view
  useEffect(() => {
    if (!strong || !box.current) return;
    const row = box.current.querySelector<HTMLElement>(`[data-line="${strong[0]}"]`);
    if (!row) return;
    const top = row.offsetTop;              // the box is position:relative
    const view = box.current;
    if (top < view.scrollTop + 20 || top > view.scrollTop + view.clientHeight - 60)
      view.scrollTo({ top: Math.max(0, top - view.clientHeight / 3), behavior: "smooth" });
  }, [strong]);

  const rows = [];
  for (let n = firstLine; n <= last; n++) {
    const isStrong = strong && n >= strong[0] && n <= strong[1];
    const inScope = scope && n >= scope[0] && n <= scope[1];
    rows.push(
      <div key={n} data-line={n} onClick={() => onLine?.(n)}
           className={`flex cursor-pointer ${onLine ? "hover:bg-[var(--surface-2)]" : ""}`}
           style={{ background: isStrong ? "var(--hl-strong)" : inScope ? "var(--hl-scope)" : undefined,
                    boxShadow: isStrong ? "inset 4px 0 0 var(--gold-ink)" : undefined }}>
        <span className="select-none text-right pr-3 pl-2 w-12 shrink-0 muted">{n}</span>
        <span className="whitespace-pre pr-4">
          {tokens[n - 1]?.map((t, i) => <span key={i} style={t.c ? { color: COLORS[t.c], fontWeight: t.c === "kw" ? 600 : undefined } : undefined}>{t.t}</span>)}
          {all[n - 1] === "" ? " " : null}
        </span>
      </div>,
    );
  }
  return (
    <div ref={box} className="relative mono text-[13.5px] leading-[1.65] overflow-auto max-h-[72vh] py-2 bg-[var(--surface)]"
         role="region" aria-label="Code">
      {rows}
    </div>
  );
}
