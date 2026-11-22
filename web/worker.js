// The Strata graph, live in the browser: Relate compiled to WebAssembly,
// running in this worker so loading never blocks the page.
//
// Messages in:
//   {type: "load", source}   source is one of
//       {urls: [coreUrl, findsUrl]}   parts written by `strata export-web`
//       {url}                         one bundle (`strata export-web --bundle`)
//       {file, name} or {text, name}  a bundle from a local file (read here, never sent anywhere)
//   {id, cypher, params}     a query, answered once the first part is loaded
// Messages out: {type: "progress"|"ready"|"dataset"|"failed", ...} and {id, result, ms}

import init, { RelateDb } from "./vendor/relate-wasm/relate.js?v=dev";
import { loadPart } from "./loader.js?v=dev";

let db = null;
let coreReady = false;
const waiting = [];

function answer({ id, cypher, params }) {
  const t = performance.now();
  let result;
  try {
    result = JSON.parse(db.execute(cypher, params ? JSON.stringify(params) : null));
  } catch (err) {
    result = { columns: [], rows: [], error: String((err && err.message) || err) };
  }
  postMessage({ id, result, ms: performance.now() - t });
}

const pause = () => new Promise((r) => setTimeout(r, 0));

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

/** A bundle is {format: "strata-web", parts: [...]}; a bare part has nodes and edges. */
function partsOf(json, where) {
  if (json && json.format === "strata-web" && Array.isArray(json.parts)) {
    if (json.version !== 1) throw new Error(`${where}: strata-web version ${json.version} is newer than this viewer`);
    return { parts: json.parts, dataset: json.dataset || null };
  }
  if (json && Array.isArray(json.nodes) && Array.isArray(json.edges)) return { parts: [json], dataset: null };
  throw new Error(`${where} is not a Strata export (expected the output of "strata export-web")`);
}

// `parts` may hold promises: a part still downloading is awaited only when its turn comes.
async function loadParts(parts) {
  for (const [i, pending] of parts.entries()) {
    const part = await pending;
    const name = (part.meta && part.meta.part) || `part ${i + 1}`;
    const t = performance.now();
    const counts = await loadPart(db, part, {
      pause,
      onProgress: ({ done, total }) => postMessage({ type: "progress", part: name, index: i, done, total }),
    });
    postMessage({ type: "ready", part: name, index: i, last: i === parts.length - 1, ...counts, loadMs: performance.now() - t });
    if (i === 0) {
      coreReady = true;
      waiting.splice(0).forEach(answer);
    }
  }
}

async function load(source) {
  await init({ module_or_path: new URL("./vendor/relate-wasm/relate_bg.wasm?v=dev", import.meta.url) });
  db = new RelateDb();
  if (source.urls) {
    // Start every download now; load each part as soon as its turn comes.
    const parts = source.urls.map((u) => fetchJson(u).then((j) => partsOf(j, u).parts[0]));
    parts.forEach((p) => p.catch(() => {}));  // a failure surfaces when that part is awaited
    await loadParts(parts);
  } else if (source.url) {
    const b = partsOf(await fetchJson(source.url), source.url);
    postMessage({ type: "dataset", dataset: b.dataset });
    await loadParts(b.parts);
  } else if (source.file || typeof source.text === "string") {
    const text = source.file ? await source.file.text() : source.text;
    let json;
    try { json = JSON.parse(text); } catch (e) { throw new Error(`${source.name} is not JSON: ${e.message}`); }
    const b = partsOf(json, source.name);
    postMessage({ type: "dataset", dataset: b.dataset });
    await loadParts(b.parts);
  } else {
    throw new Error("nothing to load");
  }
}

onmessage = (e) => {
  const msg = e.data;
  if (msg.type === "load") {
    load(msg.source).catch((err) => postMessage({ type: "failed", error: String((err && err.message) || err) }));
  } else if (coreReady) {
    answer(msg);
  } else {
    waiting.push(msg);
  }
};
