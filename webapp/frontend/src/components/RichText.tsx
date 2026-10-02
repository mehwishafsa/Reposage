// Explanations mark code with `backticks`; show those parts as code.
export default function RichText({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2
          ? <code key={i} className="px-1 bg-[var(--surface-2)] border border-[var(--bevel-lo)] text-[0.92em] break-words">{p.slice(1, -1)}</code>
          : <span key={i}>{p}</span>,
      )}
    </>
  );
}
