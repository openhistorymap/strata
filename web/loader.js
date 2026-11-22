// Rebuild the Strata graph inside an in-memory Relate (WASM) database from a
// JSON part written by `strata export-web`. Used by worker.js, and by
// tests/web_smoke.mjs under Node.

const CHUNK = 1000;
const ident = (s) => {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(s)) throw new Error(`unexpected identifier ${s}`);
  return s;
};

function run(db, query, params) {
  const res = JSON.parse(db.execute(query, params ? JSON.stringify(params) : null));
  if (res.error) throw new Error(`${res.error}\n  in: ${query.slice(0, 120)}`);
  return res;
}

// Properties are spelled out in the CREATE map ({uid: r.p.uid, ...}): in
// Relate 1.2 that is about four times faster than `SET n = r.p`. A missing key
// is null, and CREATE skips null properties.
const keysOf = (maps) => [...new Set(maps.flatMap((m) => Object.keys(m || {})))].map(ident);

function nodeQuery(group) {
  const props = keysOf(group.rows.map((r) => r.p)).map((k) => `${k}: r.p.${k}`);
  const points = keysOf(group.rows.map((r) => r.pt)).map(
    (k) => `${k}: CASE WHEN r.pt.${k} IS NULL THEN null ELSE point(r.pt.${k}) END`);
  return `UNWIND $rows AS r CREATE (n:${ident(group.label)} {${[...props, ...points].join(", ")}})`;
}

function edgeQuery(group) {
  const [la, ka] = group.from.map(ident);
  const [lb, kb] = group.to.map(ident);
  const props = keysOf(group.rows.map((r) => r[2])).map((k) => `${k}: r[2].${k}`);
  return `UNWIND $rows AS r MATCH (a:${la} {${ka}: r[0]}) MATCH (b:${lb} {${kb}: r[1]}) ` +
         `CREATE (a)-[:${ident(group.type)} {${props.join(", ")}}]->(b)`;
}

/**
 * Load one part into `db`. Yields between chunks (via `pause`) so a worker can
 * answer queries while the rest arrives. Reports progress as {done, total}.
 */
export async function loadPart(db, part, { onProgress = () => {}, pause = async () => {}, timings = null } = {}) {
  for (const statement of part.meta.schema || []) {
    const res = JSON.parse(db.execute(statement, null));
    if (res.error && !/already exists/i.test(res.error)) throw new Error(res.error);
  }
  const total = part.nodes.reduce((n, g) => n + g.rows.length, 0) + part.edges.reduce((n, g) => n + g.rows.length, 0);
  let done = 0;
  const jobs = [
    ...part.nodes.map((g) => [nodeQuery(g), g.rows, g.label]),
    ...part.edges.map((g) => [edgeQuery(g), g.rows, g.type]),
  ];
  for (const [query, rows, name] of jobs) {
    const t = Date.now();
    for (let i = 0; i < rows.length; i += CHUNK) {
      const slice = rows.slice(i, i + CHUNK);
      run(db, query, { rows: slice });
      done += slice.length;
      onProgress({ done, total });
      await pause();
    }
    if (timings) timings.push([name, rows.length, Date.now() - t]);
  }
  return {
    nodes: part.nodes.reduce((n, g) => n + g.rows.length, 0),
    relationships: part.edges.reduce((n, g) => n + g.rows.length, 0),
  };
}
