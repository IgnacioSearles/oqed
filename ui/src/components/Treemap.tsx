import { useMemo, useState } from "react";
import { type Tile, type TileState, area, pct } from "../project";
import { useWidth } from "../useWidth";

// Squarified treemap (Bruls, Huizing, van Wijk): rows of rectangles kept close
// to square, laid along the shorter side of what is left.
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function squarify<T>(items: { value: number; item: T }[], r: Rect): (Rect & { item: T })[] {
  const total = items.reduce((a, i) => a + i.value, 0);
  if (total <= 0 || r.w <= 0 || r.h <= 0) return [];
  const scale = (r.w * r.h) / total;
  const rest = items.map((i) => ({ area: i.value * scale, item: i.item }));
  const out: (Rect & { item: T })[] = [];
  let { x, y, w, h } = r;
  const worst = (row: number[], side: number) => {
    const s = row.reduce((a, b) => a + b, 0);
    return Math.max(...row.map((a) => Math.max((side * side * a) / (s * s), (s * s) / (side * side * a))));
  };
  while (rest.length) {
    const side = Math.min(w, h);
    const row = [rest.shift()!];
    while (rest.length && worst([...row, rest[0]].map((i) => i.area), side) <= worst(row.map((i) => i.area), side)) {
      row.push(rest.shift()!);
    }
    const sum = row.reduce((a, i) => a + i.area, 0);
    const thick = sum / side;
    let off = 0;
    for (const i of row) {
      const len = i.area / thick;
      out.push(w >= h ? { x, y: y + off, w: thick, h: len, item: i.item } : { x: x + off, y, w: len, h: thick, item: i.item });
      off += len;
    }
    if (w >= h) {
      x += thick;
      w -= thick;
    } else {
      y += thick;
      h -= thick;
    }
  }
  return out;
}

type Cell =
  | { kind: "fn"; tile: Tile }
  | { kind: "small"; tiles: Tile[]; file: string }
  | { kind: "outside"; share: number };

const STATE_CLASS: Record<TileState, string> = {
  won: "bg-good-bg border-good text-good",
  searching: "bg-accent-bg border-accent text-accent oqed-pulse",
  nowin: "bg-select border-rule-soft text-ink-3",
  queued: "bg-panel border-accent border-dashed text-accent",
  idle: "bg-panel border-rule-soft text-ink-2",
  skipped: "oqed-hatch border-rule-soft text-ink-3",
};

export const STATE_LABEL: Record<TileState, string> = {
  won: "faster version locked",
  searching: "searching now",
  nowin: "searched, no win",
  queued: "queued",
  idle: "not searched this run",
  skipped: "can't search yet",
};

const HEADER = 15; // px of file name above each file's functions
const SMALL = 0.004; // functions below this share fold into one tile per file

export function Treemap({
  tiles,
  now,
  onOpen,
  outside: showOutside = true,
}: {
  outside?: boolean; // draw the share outside the project (libc, ...) as a tile
  tiles: Tile[];
  now: number | null; // playback time: tiles flash when a candidate was just caught
  onOpen: (name: string) => void;
}) {
  const [ref, width, node] = useWidth<HTMLDivElement>();
  const height = Math.max(320, Math.min(620, width * 0.52));
  const [hover, setHover] = useState<{ cell: Cell; x: number; y: number } | null>(null);

  const layout = useMemo(() => {
    if (!width) return { files: [], cells: [] };
    const byFile = new Map<string, Tile[]>();
    for (const t of tiles) if (area(t.fn) > 0) byFile.set(t.file, [...(byFile.get(t.file) ?? []), t]);
    const inside = tiles.reduce((a, t) => a + area(t.fn), 0);
    const groups: { value: number; item: { file: string; tiles: Tile[] } | null }[] = [...byFile]
      .map(([file, ts]) => ({ value: ts.reduce((a, t) => a + area(t.fn), 0), item: { file, tiles: ts } }))
      .sort((a, b) => b.value - a.value);
    const outside = Math.max(0, 1 - inside);
    if (showOutside && outside > 0.005) groups.push({ value: outside, item: null });
    const files: (Rect & { file: string | null })[] = [];
    const cells: (Rect & { cell: Cell })[] = [];
    for (const g of squarify(groups, { x: 0, y: 0, w: width, h: height })) {
      files.push({ ...g, file: g.item?.file ?? null });
      if (!g.item) {
        cells.push({ ...g, cell: { kind: "outside", share: outside } });
        continue;
      }
      const head = g.h > 48 && g.w > 60 ? HEADER : 0;
      const big = g.item.tiles.filter((t) => area(t.fn) >= SMALL || t.state === "won" || t.state === "searching");
      const small = g.item.tiles.filter((t) => !big.includes(t));
      const items: { value: number; item: Cell }[] = big
        .sort((a, b) => area(b.fn) - area(a.fn))
        .map((t) => ({ value: area(t.fn), item: { kind: "fn", tile: t } }));
      if (small.length) {
        items.push({
          value: small.reduce((a, t) => a + area(t.fn), 0),
          item: { kind: "small", tiles: small, file: g.item.file },
        });
      }
      for (const c of squarify(items, { x: g.x, y: g.y + head, w: g.w, h: g.h - head })) {
        cells.push({ ...c, cell: c.item });
      }
    }
    return { files, cells };
  }, [tiles, width, height, showOutside]);

  return (
    <div ref={ref} className="relative w-full select-none" style={{ height }} onMouseLeave={() => setHover(null)}>
      {layout.files.map((f) =>
        f.file && f.h > 48 && f.w > 60 ? (
          <div
            key={f.file}
            className="absolute truncate border-x border-t border-rule bg-panel-head px-1 text-[11px] leading-[15px] font-bold text-ink-2"
            style={{ left: f.x, top: f.y, width: f.w, height: HEADER }}
          >
            {f.file}
          </div>
        ) : null,
      )}
      {layout.cells.map((c, i) => {
        const key = c.cell.kind === "fn" ? c.cell.tile.fn.name : c.cell.kind === "small" ? `small:${c.cell.file}` : "outside";
        const roomy = c.w > 54 && c.h > 30;
        const tall = c.h > 46;
        let cls = "";
        let label: React.ReactNode = null;
        let flash = false;
        if (c.cell.kind === "fn") {
          const t = c.cell.tile;
          cls = STATE_CLASS[t.state];
          flash = now != null && t.lastCaught != null && now - t.lastCaught >= 0 && now - t.lastCaught < 2;
          label = (
            <>
              <span className="block truncate font-bold">{t.fn.function}</span>
              {tall && (
                <span className="block truncate tabular">
                  {t.state === "won" && t.speedup
                    ? `${t.speedup.toFixed(2)}×${t.view?.best?.status === "proven" ? " ✓ proven" : ""}${t.earlier ? " (earlier run)" : ""}`
                    : t.state === "searching"
                      ? `${t.tried} tried`
                      : t.state === "nowin"
                        ? "no win"
                        : pct(area(t.fn))}
                </span>
              )}
            </>
          );
        } else if (c.cell.kind === "small") {
          cls = c.cell.tiles.every((t) => t.state === "skipped") ? STATE_CLASS.skipped : STATE_CLASS.idle;
          label = <span className="block truncate">{c.cell.tiles.length} small</span>;
        } else {
          cls = "oqed-hatch-dense border-rule-soft text-ink-3";
          label = (
            <>
              <span className="block truncate font-bold">outside the project</span>
              {tall && <span className="block truncate">libc, system · {pct(c.cell.share)}</span>}
            </>
          );
        }
        const clickable = c.cell.kind === "fn" && c.cell.tile.view != null;
        return (
          <button
            key={key + i}
            type="button"
            disabled={!clickable}
            onClick={() => c.cell.kind === "fn" && onOpen(c.cell.tile.fn.name)}
            onMouseMove={(e) => {
              const box = node.current!.getBoundingClientRect();
              setHover({ cell: c.cell, x: e.clientX - box.left, y: e.clientY - box.top });
            }}
            className={`absolute overflow-hidden border px-1 py-0.5 text-left text-[11px] leading-tight transition-colors duration-500 ${cls} ${
              clickable ? "cursor-pointer hover:brightness-95 dark:hover:brightness-125" : "cursor-default"
            } ${flash ? "oqed-flash" : ""}`}
            style={{ left: c.x, top: c.y, width: c.w, height: c.h, borderWidth: c.cell.kind === "fn" && c.cell.tile.state !== "idle" ? 1.5 : 1 }}
            aria-label={c.cell.kind === "fn" ? `${c.cell.tile.fn.function}: ${STATE_LABEL[c.cell.tile.state]}` : undefined}
          >
            {roomy && label}
          </button>
        );
      })}
      {hover && <Tooltip {...hover} width={width} />}
    </div>
  );
}

function Tooltip({ cell, x, y, width }: { cell: Cell; x: number; y: number; width: number }) {
  const left = x > width - 300 ? x - 290 : x + 14;
  let body: React.ReactNode;
  if (cell.kind === "fn") {
    const t = cell.tile;
    body = (
      <>
        <div className="font-bold text-ink">{t.fn.function}</div>
        <div className="text-ink-3">{t.fn.location}</div>
        <div className="mt-1 text-ink-2">
          {pct(area(t.fn))} of the samples in its own code
          {t.fn.own != null && t.fn.time > t.fn.own + 0.001 ? `, ${pct(t.fn.time)} with what was inlined into it` : ""}
        </div>
        <div className="text-ink-2">
          {STATE_LABEL[t.state]}
          {t.state === "won" && t.speedup ? `: ${t.speedup.toFixed(2)}× on the project benchmark` : ""}
        </div>
        {t.tried > 0 && <div className="text-ink-3">{t.tried} candidates tried</div>}
        {t.fn.skipped && <div className="text-ink-3">{t.fn.skipped}</div>}
        {t.view && <div className="mt-1 text-accent">click for the code</div>}
      </>
    );
  } else if (cell.kind === "small") {
    const top = [...cell.tiles].sort((a, b) => area(b.fn) - area(a.fn)).slice(0, 6);
    body = (
      <>
        <div className="font-bold text-ink">
          {cell.tiles.length} functions in {cell.file} under {pct(SMALL)} each
        </div>
        {top.map((t) => (
          <div key={t.fn.name} className="text-ink-2">
            {t.fn.function} · {pct(area(t.fn))}
          </div>
        ))}
      </>
    );
  } else {
    body = (
      <>
        <div className="font-bold text-ink">outside the project</div>
        <div className="text-ink-2">{pct(cell.share)} of the samples: libc, the kernel, the dynamic loader.</div>
        <div className="text-ink-3">oqed only rewrites the project's own code, so this part bounds the whole-program gain.</div>
      </>
    );
  }
  return (
    <div
      className="pointer-events-none absolute z-10 w-72 border border-rule bg-panel px-2 py-1.5 text-[12px] leading-snug shadow-[2px_2px_0_var(--rule-soft)]"
      style={{ left, top: y + 14 }}
    >
      {body}
    </div>
  );
}
