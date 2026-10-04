// Original pixel art for RepoSage, drawn with SVG rectangles.
// Each picture is a list of rows; every character is one pixel:
// "." = empty, other letters map to a colour in the palette.

type Palette = Record<string, string>;

function PixelArt({ rows, palette, size, title }: { rows: string[]; palette: Palette; size: number; title?: string }) {
  const w = rows[0].length, h = rows.length;
  const rects = [];
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const c = rows[y][x];
      if (c !== "." && palette[c]) rects.push(<rect key={`${x},${y}`} x={x} y={y} width={1.02} height={1.02} fill={palette[c]} />);
    }
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={size} height={(size * h) / w} shapeRendering="crispEdges"
         role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      {rects}
    </svg>
  );
}

// The RepoSage mascot: a little green block with round glasses and a leaf.
const SAGE = [
  "......ll....",
  ".....lLl....",
  "..kkkkkkkk..",
  ".kgggggggggk",
  ".kgGGGGGGGgk",
  ".kgwwkgwwkgk",
  ".kgwbkgwbkgk",
  ".kgkkgggkkgk",
  ".kggggggggk.",
  ".kggrrrrggk.",
  ".kgggggggdk.",
  "..kkkkkkkk..",
];
const SAGE_COLORS = { k: "#1c1522", g: "#5aa33c", G: "#8fd46b", d: "#2f6b1b", w: "#fffaf0", b: "#1c1522", r: "#b5523b", l: "#3f8a2a", L: "#8fd46b" };

export function SageIcon({ size = 40 }: { size?: number }) {
  return <PixelArt rows={SAGE} palette={SAGE_COLORS} size={size} title="RepoSage" />;
}

// A wooden crate (your code arriving).
const CRATE = [
  "kkkkkkkkkkkk",
  "kppPPPPPPppk",
  "kpkkkkkkkkpk",
  "kPkoPPPPokPk",
  "kPkPoPPoPkPk",
  "kPkPPooPPkPk",
  "kPkPPooPPkPk",
  "kPkPoPPoPkPk",
  "kPkoPPPPokPk",
  "kpkkkkkkkkpk",
  "kppPPPPPPppk",
  "kkkkkkkkkkkk",
];
export function CrateIcon({ size = 40 }: { size?: number }) {
  return <PixelArt rows={CRATE} palette={{ k: "#3b2a1a", p: "#a47646", P: "#c8955a", o: "#7b5230" }} size={size} />;
}

// The hopping cube shown while we read the code.
const CUBE = [
  "..kkkkkk..",
  ".kccccccck",
  "kcCCCCCCck",
  "kcCkCCkCck",
  "kcCkCCkCck",
  "kcCCCCCCck",
  "kcCCyyCCck",
  "kcccccccck",
  ".kkkkkkkk.",
];
export function CubeBuddy({ size = 64 }: { size?: number }) {
  return <PixelArt rows={CUBE} palette={{ k: "#12324a", c: "#39b6e0", C: "#7fe3ff", y: "#ffd24a" }} size={size} />;
}
