// Load a dataset into Relate WASM under Node and run every viewer query.
//   docker run --rm -v "$PWD":/app -w /app node:22-slim node tests/web_smoke.mjs
//   ... node tests/web_smoke.mjs path/to/my-dig.strata.json     (a bundle instead)
import { readFileSync } from "node:fs";
import { initSync, RelateDb } from "../web/vendor/relate-wasm/relate.js";
import { loadPart } from "../web/loader.js";
import { Q, EXAMPLES } from "../web/queries.js";

initSync({ module: readFileSync(new URL("../web/vendor/relate-wasm/relate_bg.wasm", import.meta.url)) });
const db = new RelateDb();
const bundle = process.argv[2];
const parts = bundle
  ? JSON.parse(readFileSync(bundle)).parts
  : JSON.parse(readFileSync(new URL("../web/data/datasets.json", import.meta.url))).datasets[0].parts
      .map((p) => JSON.parse(readFileSync(new URL(`../web/data/${p.path}`, import.meta.url))));
for (const part of parts) {
  const t = Date.now();
  const timings = [];
  const counts = await loadPart(db, part, { timings });
  console.log(`loaded ${part.meta.part}: ${counts.nodes} nodes, ${counts.relationships} relationships in ${Date.now() - t} ms`, JSON.stringify(timings));
}
let failed = 0;
const ask = (label, cypher, params) => {
  const t = Date.now();
  const res = JSON.parse(db.execute(cypher, params ? JSON.stringify(params) : null));
  const ms = Date.now() - t;
  if (res.error) { failed++; console.log(`FAIL ${label}: ${res.error}`); return []; }
  console.log(`ok   ${label}: ${res.rows.length} rows, ${ms} ms  ${JSON.stringify(res.rows[0] ?? null).slice(0, 150)}`);
  return res.rows;
};
const [site] = ask("site", Q.site);
const groups = ask("trenches", Q.trenches);
const kenan = site && site.id === "kenan-tepe";
const unit = kenan
  ? ask("lookup", Q.byPath, { path: "Area F/Trench 1/Locus 1032" })[0]
  : ask("lookup", "MATCH (c:Context)-[:IN_TRENCH|IN_SITE]->(g) RETURN c.uid AS uid, coalesce(g.uid, g.id) AS trench ORDER BY c.depth DESC LIMIT 1")[0];
if (!unit || !groups.length) { console.log("FAIL no unit or no groups"); process.exit(1); }
const params = { uid: unit.uid, trench: unit.trench, kind: kenan ? "period_inversion" : "cycle", path: "x" };
for (const [name, cypher] of Object.entries(Q)) ask(name, cypher, params);
if (kenan) {
  ask("matrixUnits D5", Q.matrixUnits, { trench: "kenan-tepe:Area D/Trench 5" });
  for (const [label, cypher] of EXAMPLES) ask(label, cypher);
}
process.exit(failed ? 1 : 0);
