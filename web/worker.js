// The Strata graph, live in the browser: Relate compiled to WebAssembly,
// running in this worker so loading never blocks the page.
//
// Messages in:  {id, cypher, params}
// Messages out: {type: "progress"|"ready"|"failed", ...} and {id, result, ms}

import init, { RelateDb } from "./vendor/relate-wasm/relate.js";
import { loadPart } from "./loader.js";

let db = null;
let coreReady = false;
const waiting = [];

function answer({ id, cypher, params }) {
  const t = performance.now();
  let result;
  try {
    result = JSON.parse(db.execute(cypher, params ? JSON.stringify(params) : null));
  } catch (err) {
    result = { columns: [], rows: [], error: String(err && err.message || err) };
  }
  postMessage({ id, result, ms: performance.now() - t });
}

onmessage = (e) => (coreReady ? answer(e.data) : waiting.push(e.data));

const pause = () => new Promise((r) => setTimeout(r, 0));

async function load(name) {
  const t = performance.now();
  const res = await fetch(new URL(`./data/${name}.json`, import.meta.url));
  if (!res.ok) throw new Error(`data/${name}.json: HTTP ${res.status}`);
  const part = await res.json();
  const fetched = performance.now();
  const counts = await loadPart(db, part, {
    pause,
    onProgress: ({ done, total }) => postMessage({ type: "progress", part: name, done, total }),
  });
  postMessage({ type: "ready", part: name, ...counts, fetchMs: fetched - t, loadMs: performance.now() - fetched });
}

(async () => {
  try {
    await init({ module_or_path: new URL("./vendor/relate-wasm/relate_bg.wasm", import.meta.url) });
    db = new RelateDb();
    await load("core");
    coreReady = true;
    waiting.splice(0).forEach(answer);
    await load("finds");
  } catch (err) {
    postMessage({ type: "failed", error: String(err && err.message || err) });
  }
})();
