import { useEffect, useState } from "react";
import { CubeBuddy } from "./Pixel";

const STAGES = [
  { id: "unpacking", label: "Opening your crate of code" },
  { id: "reading", label: "Reading every file" },
  { id: "finding", label: "Finding the functions" },
  { id: "connecting", label: "Building roads between them" },
  { id: "overview", label: "Drawing your map" },
];

const TIPS = [
  "A function is a named block of code you can use again and again.",
  "A call is when one function asks another function to do a job.",
  "In C, a .h header file lists functions so other files can use them.",
  "Don't read code top to bottom - start where the program starts.",
  "A variable is a labelled box that holds a value, like username or total.",
  "An import (or #include) lets one file use code written in another file.",
  "Loops repeat steps; if/else picks one road or the other.",
];

const BLOCKS = 20;

/** Blocks drop into the bar as the server works. The bar moves at a steady
 *  pace even for tiny projects, so the screen never just flickers past. */
export default function Loading({ stage, progress, onShown }: { stage: string; progress: number; onShown: () => void }) {
  const [shown, setShown] = useState(0);
  const [tip, setTip] = useState(() => Math.floor(Math.random() * TIPS.length));
  const target = Math.round((progress / 100) * BLOCKS);

  useEffect(() => {
    if (shown >= BLOCKS) {
      const t = setTimeout(onShown, 250);
      return () => clearTimeout(t);
    }
    if (shown < target) {
      const t = setTimeout(() => setShown((n) => n + 1), 90);
      return () => clearTimeout(t);
    }
  }, [shown, target, onShown]);

  useEffect(() => {
    const t = setInterval(() => setTip((i) => (i + 1) % TIPS.length), 3500);
    return () => clearInterval(t);
  }, []);

  const shownPct = (shown / BLOCKS) * 100;
  const finished = shown >= BLOCKS;
  const activeIndex = Math.min(STAGES.length - 1, Math.floor((shownPct / 100) * STAGES.length));
  const serverStage = STAGES.find((s) => s.id === stage)?.label ?? "Getting ready";

  return (
    <div className="max-w-2xl mx-auto px-4 py-10 sm:py-16" aria-live="polite">
      <div className="tile p-6 sm:p-8 text-center">
        <div className="hop inline-block"><CubeBuddy size={72} /></div>
        <h1 className="pixel text-[15px] sm:text-[18px] mt-4 mb-2">Reading your code<span className="dots" /></h1>
        <p className="muted m-0">This usually takes a few seconds.</p>
        <p className="sr-only">Now: {serverStage}</p>

        <div className="mt-6 flex gap-[3px] p-[5px] border-3 border-[var(--edge)] bg-[var(--surface-2)]" role="progressbar"
             aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(shownPct)} aria-label="Progress">
          {Array.from({ length: BLOCKS }, (_, i) => (
            <span key={i} className={`flex-1 h-6 ${i < shown ? "drop-in" : ""}`}
                  style={{ background: i < shown ? (i % 2 ? "var(--grass)" : "var(--grass-hi)") : "transparent",
                           boxShadow: i < shown ? "inset -2px -2px 0 var(--grass-lo)" : "none" }} />
          ))}
        </div>

        <ol className="text-left mt-6 space-y-2 p-0 list-none">
          {STAGES.map((s, i) => {
            const done = i < activeIndex || finished;
            const active = i === activeIndex && !done;
            return (
              <li key={s.id} className={`flex items-center gap-3 ${done ? "" : active ? "font-bold" : "muted"}`}>
                <span className="inline-block w-4 h-4 border-2 border-[var(--edge)]"
                      style={{ background: done ? "var(--grass)" : active ? "var(--gold)" : "transparent" }} />
                {s.label}{active && <span className="dots" />}
              </li>
            );
          })}
        </ol>

        <div className="tile-flat mt-6 p-4 text-left">
          <span className="pixel text-[10px] text-[var(--grass)]">TIP</span>
          <p className="m-0 mt-1">{TIPS[tip]}</p>
        </div>
      </div>
    </div>
  );
}
