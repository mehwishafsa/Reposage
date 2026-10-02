import type { Cite } from "../api";

// Explanations mark code with `backticks`; show those parts as code.
// Chat answers also contain citations like [calc.c:22-26]: those become
// buttons that open the code with the lines highlighted.
const PATTERN = /(`[^`]+`|\[[\w./\\-]+\.\w+:\d+(?:-\d+)?\])/g;

export default function RichText({ text, onCite }: { text: string; onCite?: (c: Cite) => void }) {
  const parts = text.split(PATTERN);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("`") && p.endsWith("`") && p.length > 2)
          return <code key={i} className="px-1 bg-[var(--surface-2)] border border-[var(--bevel-lo)] text-[0.92em] break-words">{p.slice(1, -1)}</code>;
        const m = /^\[([\w./\\-]+\.\w+):(\d+)(?:-(\d+))?\]$/.exec(p);
        if (m && onCite) {
          const cite = { file: m[1], start: +m[2], end: +(m[3] ?? m[2]) };
          return (
            <button key={i} className="cite" onClick={() => onCite(cite)} title="Show this code">
              {m[1].split("/").pop()}:{m[2]}{m[3] ? `-${m[3]}` : ""}
            </button>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}
