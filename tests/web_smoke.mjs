// Load web/data/*.json into Relate WASM under Node and run every viewer query.
//   docker run --rm -v "$PWD":/app -w /app node:22-slim node tests/web_smoke.mjs
import { readFileSync } from "node:fs";
import { initSync, RelateDb } from "../web/vendor/relate-wasm/relate.js";
import { loadPart } from "../web/loader.js";
import { Q, EXAMPLES } from "../web/queries.js";

initSync({ module: readFileSync(new URL("../web/vendor/relate-wasm/relate_bg.wasm", import.meta.url)) });
const db = new RelateDb();
for (const name of ["core", "finds"]) {
  const t = Date.now();
  const part = JSON.parse(readFileSync(new URL(`../web/data/${name}.json`, import.meta.url)));
  const timings = [];
  const counts = await loadPart(db, part, { timings });
  console.log(`loaded ${name}: ${counts.nodes} nodes, ${counts.relationships} relationships in ${Date.now() - t} ms`, JSON.stringify(timings));
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
const unit = ask("lookup", "MATCH (c:Context {path: 'Area F/Trench 1/Locus 1032'}) RETURN c.uid AS uid, c.trench AS trench")[0];
const trench = "kenan-tepe:" + unit.trench;
const params = { uid: unit.uid, trench, kind: "period_inversion" };
for (const [name, cypher] of Object.entries(Q)) ask(name, cypher, params);
ask("matrixUnits D5", Q.matrixUnits, { trench: "kenan-tepe:Area D/Trench 5" });
for (const [label, cypher] of EXAMPLES) ask(label, cypher);
process.exit(failed ? 1 : 0);
