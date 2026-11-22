// Harris-matrix layout for one trench.
//
// Rows come from the graph: Context.depth is each unit's row in the site
// matrix, computed by Strata's checks (Kahn peeling over :ABOVE). Rows are
// compressed to the ones this trench uses. Order within a row is a few
// barycentre sweeps to cut crossings. Units related to nothing go in a tray
// at the bottom.

const NODE_W = 62, NODE_H = 22, GAP_X = 12, GAP_Y = 48, TRAY_COLS = 14;

const labelKey = (label) => {
  const m = /(\d+)/.exec(label || "");
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
};

export function layout(units, edges) {
  const order = (edges || []).filter((e) => (e.type === "ABOVE" && !e.redundant) || e.type === "CONTESTED");
  const related = new Set();
  for (const e of edges) if (e.type !== "REJECTED") { related.add(e.a); related.add(e.b); }

  const placed = units.filter((u) => related.has(u.uid));
  const tray = units.filter((u) => !related.has(u.uid)).sort((a, b) => labelKey(a.label) - labelKey(b.label));

  const depths = [...new Set(placed.map((u) => u.depth ?? 0))].sort((a, b) => a - b);
  const rowOf = new Map(depths.map((d, i) => [d, i]));
  const rows = depths.map(() => []);
  for (const u of placed) rows[rowOf.get(u.depth ?? 0)].push(u.uid);
  const label = new Map(units.map((u) => [u.uid, u.label]));
  for (const r of rows) r.sort((a, b) => labelKey(label.get(a)) - labelKey(label.get(b)));

  const up = new Map(), down = new Map();
  for (const e of order) {
    if (!up.has(e.b)) up.set(e.b, []);
    if (!down.has(e.a)) down.set(e.a, []);
    up.get(e.b).push(e.a);
    down.get(e.a).push(e.b);
  }
  const x = new Map();
  const setX = () => rows.forEach((r) => r.forEach((id, i) => x.set(id, i - (r.length - 1) / 2)));
  setX();
  const bary = (id, nbrs) => {
    const xs = (nbrs.get(id) || []).filter((n) => x.has(n)).map((n) => x.get(n));
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : x.get(id);
  };
  for (let pass = 0; pass < 8; pass++) {
    const range = pass % 2 === 0 ? rows.keys() : [...rows.keys()].reverse();
    for (const i of range) {
      const nbrs = pass % 2 === 0 ? up : down;
      const key = new Map(rows[i].map((id) => [id, bary(id, nbrs)]));
      rows[i].sort((a, b) => key.get(a) - key.get(b) || labelKey(label.get(a)) - labelKey(label.get(b)));
      rows[i].forEach((id, j) => x.set(id, j - (rows[i].length - 1) / 2));
    }
  }

  const widest = Math.max(1, ...rows.map((r) => r.length), Math.min(TRAY_COLS, tray.length));
  const width = widest * (NODE_W + GAP_X) + 80;
  const cx = width / 2;
  const pos = new Map();
  const rowYs = [], rowNames = [];
  rows.forEach((r, i) => {
    const y = 30 + i * (NODE_H + GAP_Y);
    rowYs.push(y);
    rowNames.push(String(depths[i]));
    r.forEach((id, j) => pos.set(id, { x: cx + (j - (r.length - 1) / 2) * (NODE_W + GAP_X), y }));
  });
  let height = 30 + rows.length * (NODE_H + GAP_Y);
  if (tray.length) {
    height += 20;
    const trayRows = Math.ceil(tray.length / TRAY_COLS);
    for (let k = 0; k < trayRows; k++) {
      const slice = tray.slice(k * TRAY_COLS, (k + 1) * TRAY_COLS);
      const y = height + k * (NODE_H + 12);
      rowYs.push(y);
      rowNames.push(k === 0 ? "unrelated" : "");
      slice.forEach((u, j) => pos.set(u.uid, { x: cx + (j - (slice.length - 1) / 2) * (NODE_W + GAP_X), y }));
    }
    height += trayRows * (NODE_H + 12);
  }
  return { pos, width, height: height + 10, nodeW: NODE_W, nodeH: NODE_H, rowYs, rowNames };
}
