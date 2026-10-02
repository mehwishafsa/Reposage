import { useEffect, useRef, useState } from "react";

// Mermaid is big, so it is loaded only when a flowchart is shown.
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  mermaidPromise ??= import("mermaid").then((m) => m.default);
  return mermaidPromise;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Watch the light/dark theme so the chart can be redrawn in the right colours. */
function useThemeKey(): string {
  const [key, setKey] = useState(() => document.documentElement.dataset.theme ?? "auto");
  useEffect(() => {
    const obs = new MutationObserver(() => setKey(document.documentElement.dataset.theme ?? "auto"));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return key;
}

let counter = 0;

/** A flowchart from Mermaid text. Our own click handling (no Mermaid "click"
 *  callbacks), so Mermaid can run in strict security mode. */
export default function Flowchart({ code, selected, onSelect, title }: {
  code: string; selected: string | null; onSelect: (id: string) => void; title: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<number | null>(null);      // null = automatic
  const natural = useRef(0);
  const theme = useThemeKey();
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mermaid = await loadMermaid();
      const c = {
        surface: cssVar("--surface"), surface2: cssVar("--surface-2"), ink: cssVar("--ink"), edge: cssVar("--edge"),
        grass: cssVar("--grass"), gold: cssVar("--gold"), stone: cssVar("--stone"), sky: cssVar("--sky"),
        purple: cssVar("--c-c"), danger: cssVar("--danger"), muted: cssVar("--muted"),
      };
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict", theme: "base",
        fontFamily: 'ui-monospace, "Cascadia Code", Consolas, Menlo, monospace',
        flowchart: { curve: "linear", padding: 12, nodeSpacing: 34, rankSpacing: 42, useMaxWidth: false },
        themeVariables: {
          fontSize: "14px", primaryColor: c.surface, primaryBorderColor: c.edge, primaryTextColor: c.ink,
          lineColor: c.stone, textColor: c.ink, edgeLabelBackground: c.surface2, tertiaryColor: c.surface2,
        },
      });
      const styled = code + `
  classDef default stroke:${c.edge},stroke-width:2.5px,color:${c.ink},fill:${c.surface}
  classDef k_start fill:${c.grass},color:#fff,stroke:${c.edge},stroke-width:2.5px
  classDef k_end fill:${c.grass},color:#fff,stroke:${c.edge},stroke-width:2.5px
  classDef k_decision fill:${c.gold},color:#2b2233,stroke:${c.edge},stroke-width:2.5px
  classDef k_switch fill:${c.gold},color:#2b2233,stroke:${c.edge},stroke-width:2.5px
  classDef k_loop fill:${c.sky},color:${c.ink},stroke:${c.edge},stroke-width:2.5px
  classDef k_return fill:${c.surface2},color:${c.ink},stroke:${c.grass},stroke-width:3px
  classDef k_stop fill:${c.surface2},color:${c.danger},stroke:${c.danger},stroke-width:3px
  classDef k_try fill:${c.surface2},color:${c.ink},stroke:${c.purple},stroke-width:2.5px`;
      try {
        const { svg } = await mermaid.render(`fc${++counter}`, styled);
        if (cancelled || !host.current) return;
        host.current.innerHTML = svg;
        const el = host.current.querySelector("svg");
        natural.current = el?.viewBox.baseVal.width || el?.getBoundingClientRect().width || 0;
        sizeSvg();
        setError("");
        // make each box clickable and keyboard-focusable
        host.current.querySelectorAll<SVGGElement>("g.node").forEach((g) => {
          const id = boxId(g);
          if (!id) return;
          g.dataset.box = id;
          g.setAttribute("tabindex", "0");
          g.setAttribute("role", "button");
          g.style.cursor = "pointer";
          g.addEventListener("click", () => onSelectRef.current(id));
          g.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelectRef.current(id); }
          });
        });
      } catch {
        if (!cancelled) setError("This flowchart couldn't be drawn. The step-by-step explanation still works.");
      }
    })();
    return () => { cancelled = true; };
  }, [code, theme]);

  // Size: readable by default (never smaller than 70%), scroll sideways if wider.
  function sizeSvg() {
    const el = host.current?.querySelector("svg");
    if (!el || !natural.current || !host.current) return;
    const fit = Math.min(1, (host.current.clientWidth - 16) / natural.current);
    const scale = zoom ?? Math.max(0.7, fit);
    el.style.maxWidth = "none";
    el.style.width = `${natural.current * scale}px`;
    el.removeAttribute("height");
  }
  useEffect(sizeSvg, [zoom]);

  // highlight the chosen box (Mermaid puts !important inline styles on shapes,
  // so the highlight is set inline too, and the original style is restored after)
  useEffect(() => {
    const danger = cssVar("--danger");
    host.current?.querySelectorAll<SVGGElement>("g.node").forEach((g) => {
      const shape = g.querySelector<SVGElement>(":scope > .label-container, :scope > rect, :scope > polygon, :scope > path");
      const on = g.dataset.box === selected;
      g.classList.toggle("fc-selected", on);
      if (!shape) return;
      if (shape.dataset.orig === undefined) shape.dataset.orig = shape.getAttribute("style") ?? "";
      shape.setAttribute("style", on ? `${shape.dataset.orig};stroke:${danger} !important;stroke-width:6px !important`
                                     : shape.dataset.orig);
      if (on && host.current) {                // scroll the chart only, never the page
        const box = host.current.getBoundingClientRect(), r = g.getBoundingClientRect();
        const dx = r.left + r.width / 2 - (box.left + box.width / 2);
        const dy = r.top + r.height / 2 - (box.top + box.height / 2);
        host.current.scrollBy({ left: dx, top: dy, behavior: "smooth" });
      }
    });
  }, [selected, code, theme, error]);

  const step = (d: number) => {
    const current = zoom ?? Math.max(0.7, Math.min(1, ((host.current?.clientWidth ?? 600) - 16) / (natural.current || 1)));
    setZoom(Math.min(2, Math.max(0.3, Math.round((current + d) * 10) / 10)));
  };

  return (
    <div>
      {error && <p className="notice">{error}</p>}
      <div className="flex items-center gap-2 mt-3">
        <span className="muted text-sm flex-1">Click a box to see its code.</span>
        <button className="btn !py-0.5 !px-2.5 text-sm" onClick={() => step(-0.1)} aria-label="Zoom out">−</button>
        <button className="btn !py-0.5 !px-2.5 text-sm" onClick={() => step(0.1)} aria-label="Zoom in">+</button>
        <button className="btn !py-0.5 !px-2.5 text-sm" onClick={() => { setZoom(Math.min(1, ((host.current?.clientWidth ?? 600) - 16) / (natural.current || 1))); }}>Fit</button>
      </div>
      <div ref={host} className="flowchart overflow-auto max-h-[62vh] p-2 mt-2 border-2 border-dashed border-[var(--bevel-lo)]"
           role="group" aria-label={title} />
    </div>
  );
}

/** Mermaid gives each box an id like "fc3-flowchart-n7-12"; we want "n7". */
function boxId(g: Element): string | null {
  const m = /flowchart-(n\d+)-/.exec(g.id) ?? /^(n\d+)$/.exec(g.getAttribute("data-id") ?? "");
  return m ? m[1] : null;
}
