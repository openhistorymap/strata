// Strata viewer: every panel is a Cypher query against Relate (WASM) in a worker.

import { GraphClient } from "./db.js?v=dev";
import { Q, EXAMPLES } from "./queries.js?v=dev";
import { layout } from "./matrix.js?v=dev";
import { registerTools } from "./webmcp.js?v=dev";

// ------------------------------------------------------------------ helpers --

const $ = (sel) => document.querySelector(sel);
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const SVG = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of children) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
const fmt = (n) => Number(n).toLocaleString("en");
const year = (y) => (y === null || y === undefined ? "?" : y <= 0 ? `${fmt(1 - y)} BCE` : `${fmt(y)} CE`);

const KIND_TEXT = {
  self_contradiction: "One record lists the same unit as both above and below it.",
  conflicting_records: "Two records order the same pair in opposite directions.",
  cut_or_cover: "A cuts B by one record, B lies above A by another.",
  cycle: "A longer loop: A above B above C above A.",
  same_as_conflict: "Recorded as one unit, yet one lies above the other.",
  elevation_conflict: "Recorded above a unit whose bottom is higher than its own top.",
  period_inversion: "An earlier period lying above a later one.",
  enclosed: "Above, below and within the same unit: an inclusion, not an order.",
};
// Kenan Tepe's periods keep fixed colours; any other dataset's periods are
// coloured in date order from the same earthy palette.
const KNOWN_PERIODS = {
  "Ubaid": "#8c5a3c",
  "Late Chalcolithic": "#b5763f",
  "Late Chalcolithic V": "#cf9d4f",
  "Early Bronze Age I": "#8a9a5b",
  "Early Bronze Age": "#5f8a6e",
  "Middle Bronze Age": "#5d7d9a",
  "Early Iron Age": "#7d6a9e",
};
const PALETTE = ["#8c5a3c", "#b5763f", "#cf9d4f", "#8a9a5b", "#5f8a6e", "#4f8a8b", "#5d7d9a", "#7d6a9e", "#9c5f7d", "#a0524a"];
let periodColors = { ...KNOWN_PERIODS };
const periodColor = (name) => periodColors[name] || "#b7ad9f";
function assignPeriodColors(periods) {
  periodColors = { ...KNOWN_PERIODS };
  let i = 0;
  for (const p of periods) if (!periodColors[p.name]) periodColors[p.name] = PALETTE[i++ % PALETTE.length];
}

/** Only http(s) links from a dataset become hrefs: a file can carry anything. */
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
const bytes = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} kB`);

// -------------------------------------------------------------------- theme --

const THEMES = ["auto", "light", "dark"];
const themeBtn = $("#theme");
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  const mode = localStorage.getItem("strata-theme") || "auto";
  document.documentElement.dataset.theme = mode === "auto" ? (darkQuery.matches ? "dark" : "light") : mode;
  themeBtn.textContent = { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" }[mode];
  themeBtn.title = `Theme: ${mode}${mode === "auto" ? " (follows your system)" : ""}. Click to change.`;
}
themeBtn.addEventListener("click", () => {
  const mode = localStorage.getItem("strata-theme") || "auto";
  localStorage.setItem("strata-theme", THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length]);
  applyTheme();
});
darkQuery.addEventListener("change", applyTheme);
applyTheme();

// -------------------------------------------------------------------- state --

const state = {
  trench: null,         // trench uid
  unit: null,           // unit uid
  units: new Map(),     // uid -> unit row of the current trench
  edges: [],
  findsReady: false,
  trenches: [],
  source: null,         // what is loaded: {kind: "builtin"|"url"|"local", ...}
};

const engine = $("#engine");
const engineText = $("#engine-text");
const engineBar = $("#engine-bar");
let loadStats = {};
let db = null;
let catalogue = [];     // web/data/datasets.json

function onEngine(msg) {
  if (msg.type === "dataset") {
    if (msg.dataset) { state.source.dataset = msg.dataset; renderSource(); }
  } else if (msg.type === "progress") {
    engineBar.style.width = `${Math.round((100 * msg.done) / msg.total)}%`;
    engineText.textContent = msg.index === 0
      ? `Loading the matrix into Relate… ${fmt(msg.done)} / ${fmt(msg.total)}`
      : `Matrix ready · loading finds ${Math.round((100 * msg.done) / msg.total)}%`;
  } else if (msg.type === "ready") {
    loadStats[msg.index] = msg;
    if (msg.index === 0) {
      engine.classList.add("ready");
      engineBar.style.width = "0";
      start();
    }
    if (msg.last) {
      state.findsReady = true;
      engine.classList.add("done");
      if (msg.index > 0 && state.unit) showUnit(state.unit, { keepView: true });
    }
    const nodes = Object.values(loadStats).reduce((n, p) => n + p.nodes, 0);
    const rels = Object.values(loadStats).reduce((n, p) => n + p.relationships, 0);
    const secs = Object.values(loadStats).reduce((n, p) => n + p.loadMs, 0) / 1000;
    engineText.textContent = `Relate 1.2 (WebAssembly) in this tab · ${fmt(nodes)} nodes · ${fmt(rels)} relationships`
      + (state.findsReady ? "" : " · finds loading…");
    engine.title = `Loaded in ${secs.toFixed(1)} s`;
  } else if (msg.type === "failed") {
    engine.classList.add("failed");
    engineText.textContent = `Could not load: ${msg.error}`;
    $("#matrix-empty").hidden = false;
    $("#matrix-empty").textContent = msg.error;
  }
}

// ---------------------------------------------------------------- datasets --

/** Open a dataset: drop the current graph and load a fresh one in a new worker. */
function openSource(source) {
  if (db) db.close();
  state.source = source;
  Object.assign(state, { trench: null, unit: null, units: new Map(), edges: [], findsReady: false, trenches: [], reach: null, involved: [] });
  loadStats = {};
  engine.classList.remove("ready", "done", "failed");
  engineBar.style.width = "0";
  engineText.textContent = "Starting Relate (WebAssembly)…";
  svg.replaceChildren();
  $("#matrix-empty").hidden = false;
  $("#matrix-empty").textContent = "Loading the graph…";
  $("#matrix-title").textContent = "Harris matrix";
  $("#matrix-meta").textContent = "";
  $("#trench-list").replaceChildren();
  $("#issue-kinds").replaceChildren();
  $("#issue-count").textContent = "";
  $("#legend").replaceChildren();
  showPlaceholder();
  renderSource();
  db = new GraphClient(onEngine);
  db.load(source.load);
}

function builtinSource(d) {
  const base = new URL("./data/", location.href);
  // The build time versions each part, so a new export is never mixed with a cached old one.
  const v = encodeURIComponent(d.built || "");
  return { kind: "builtin", id: d.id, dataset: d, load: { urls: d.parts.map((p) => new URL(`${p.path}?v=${v}`, base).href) } };
}

function setQuery(params) {
  const url = new URL(location.href);
  url.search = new URLSearchParams(params).toString();
  history.replaceState(null, "", url);
}

function openBuiltin(id) {
  const d = catalogue.find((x) => x.id === id) || catalogue[0];
  if (!d) return;
  setQuery(d === catalogue[0] ? {} : { dataset: d.id });
  openSource(builtinSource(d));
}

function openUrl(raw) {
  let url;
  try { url = new URL(raw, location.href); } catch { return showDialogError(`Not a URL: ${raw}`); }
  if (!/^https?:$/.test(url.protocol)) return showDialogError("Only http(s) URLs can be opened.");
  setQuery({ data: url.href });
  openSource({ kind: "url", url: url.href, load: { url: url.href } });
}

function openFile(file) {
  if (!file) return;
  setQuery({});
  location.hash = "";
  openSource({ kind: "local", name: file.name, size: file.size, modified: file.lastModified, load: { file, name: file.name } });
}

function renderSource() {
  const src = state.source;
  const d = src.dataset || {};
  const chip = $("#source");
  chip.classList.toggle("local", src.kind === "local");
  let tag, title, detail;
  if (src.kind === "local") {
    tag = "Local file";
    title = src.name;
    detail = `${bytes(src.size)}${src.modified ? ` · modified ${new Date(src.modified).toISOString().slice(0, 10)}` : ""}`
      + " · read in this tab, nothing is uploaded";
  } else if (src.kind === "url") {
    tag = "URL";
    title = d.name || new URL(src.url).pathname.split("/").pop();
    detail = src.url;
  } else {
    tag = "Dataset";
    title = d.name || src.id;
    const size = (d.parts || []).reduce((n, p) => n + (p.bytes || 0), 0);
    detail = `exported from ${d.store || "data/strata.rl"}${d.built ? ` on ${d.built.slice(0, 10)}` : ""}${size ? ` · ${bytes(size)}` : ""}`;
  }
  chip.replaceChildren(h("span", { class: "tag" }, tag), h("span", { class: "title" }, title), h("span", { class: "caret", "aria-hidden": "true" }, "▾"));
  chip.title = `${tag}: ${title}\n${detail}\nClick to open another dataset`;
  const credit = [d.source, d.license].filter(Boolean).join(" · ");
  $("#site-line").textContent = [detail, credit].filter(Boolean).join(" — ");
}

// The "Open a dataset" dialog: built-in datasets, a URL, or a local file.
const dialog = $("#open-dialog");
function showDialogError(text) {
  $("#open-error").textContent = text;
  $("#open-error").hidden = !text;
}
function renderCatalogue() {
  $("#builtin-list").replaceChildren(...catalogue.map((d) => {
    const n = d.parts.reduce((a, p) => a + p.nodes, 0), r = d.parts.reduce((a, p) => a + p.relationships, 0);
    const size = d.parts.reduce((a, p) => a + p.bytes, 0);
    const current = state.source?.kind === "builtin" && state.source.id === d.id;
    return h("li", {}, h("button", { type: "button", class: "dataset", "aria-current": current ? "true" : "false",
      onclick: () => { dialog.close(); openBuiltin(d.id); } },
      h("b", {}, d.name), h("span", {}, d.source || ""),
      h("small", {}, `${fmt(n)} nodes · ${fmt(r)} relationships · ${bytes(size)} · ${d.license || "licence not stated"}`),
      h("code", {}, `${d.store || "strata.rl"} → web/data/${d.id}/`)));
  }));
}
$("#source").addEventListener("click", () => { showDialogError(""); renderCatalogue(); dialog.showModal(); });
$("#open-close").addEventListener("click", () => dialog.close());
$("#open-url-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#open-url").value.trim();
  if (!v) return;
  dialog.close();
  openUrl(v);
});
$("#open-file").addEventListener("change", (e) => { dialog.close(); openFile(e.target.files[0]); e.target.value = ""; });
const drop = $("#drop");
for (const ev of ["dragenter", "dragover"]) document.addEventListener(ev, (e) => {
  if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
  e.preventDefault();
  document.body.classList.add("dropping");
});
for (const ev of ["dragleave", "drop"]) document.addEventListener(ev, (e) => {
  if (ev === "dragleave" && e.relatedTarget) return;
  document.body.classList.remove("dropping");
});
document.addEventListener("drop", (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  e.preventDefault();
  if (dialog.open) dialog.close();
  openFile(file);
});
drop.addEventListener("click", () => $("#open-file").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#open-file").click(); } });

// ----------------------------------------------------------------- startup --

async function boot() {
  try {
    const res = await fetch(new URL("./data/datasets.json", location.href), { cache: "no-cache" });
    if (res.ok) catalogue = (await res.json()).datasets || [];
  } catch { /* no catalogue: URL and local files still work */ }
  const params = new URLSearchParams(location.search);
  if (params.get("data")) openUrl(params.get("data"));
  else if (catalogue.length) openBuiltin(params.get("dataset"));
  else { showDialogError("No built-in datasets here. Open a URL or a local file."); dialog.showModal(); }
}

let examplesFilled = false;
async function start() {
  const client = db;
  const [site] = await db.rows(Q.site);
  if (client !== db) return;
  if (site && !state.source.dataset) {
    state.source.dataset = { name: site.name, source: site.source, license: site.license };
    renderSource();
  }
  $("#site-name").textContent = site
    ? `${site.name} · ${fmt(site.units)} units${site.trenches ? ` in ${site.trenches} trenches` : ""}` : "";
  const periods = await db.rows(Q.periods);
  assignPeriodColors(periods);
  renderLegend(periods);
  state.trenches = await db.rows(Q.trenches);
  renderTrenches();
  renderIssueKinds(await db.rows(Q.issueKinds));
  if (!examplesFilled) { fillExamples(); examplesFilled = true; }
  const fallback = site && site.id === "kenan-tepe" ? { path: "Area F/Trench 1/Locus 1032" } : {};
  await goTo(parseHash() || fallback);
}

function parseHash() {
  const raw = decodeURIComponent(location.hash.slice(1));
  if (!raw) return null;
  return raw.includes("/Locus") || raw.split("/").length > 2 ? { path: raw } : { trenchName: raw };
}

async function goTo({ path, trenchName, uid }) {
  if (path) {
    const [row] = await db.rows(Q.byPath, { path });
    if (row) { await showTrench(row.trench); await showUnit(row.uid); return; }
  }
  if (uid) {
    const [row] = await db.rows(Q.byUid, { uid });
    if (row) { await showTrench(row.trench); await showUnit(uid); return; }
  }
  const t = state.trenches.find((t) => t.name === trenchName) || state.trenches[0];
  if (t) await showTrench(t.uid);
}

// ------------------------------------------------------------- left panel --

function renderTrenches() {
  const list = $("#trench-list");
  const filter = $("#trench-filter").value.trim().toLowerCase();
  list.replaceChildren(...state.trenches
    .filter((t) => !filter || t.name.toLowerCase().includes(filter))
    .map((t) => h("li", {}, h("button", {
      "aria-current": t.uid === state.trench ? "true" : "false",
      onclick: () => showTrench(t.uid),
    }, h("span", { class: "name" }, t.name.replace("/", " · ")),
       t.issues ? h("span", { class: "flag", title: `${t.issues} open issues` }, `${t.issues}!`) : null,
       h("span", { class: "n" }, `${t.units}`)))));
}
$("#trench-filter").addEventListener("input", renderTrenches);

function renderIssueKinds(kinds) {
  $("#issue-count").textContent = fmt(kinds.reduce((n, k) => n + k.n, 0));
  const box = $("#issue-kinds");
  box.replaceChildren(...kinds.map((k) => {
    const list = h("ul", { hidden: true });
    const btn = h("button", { "aria-expanded": "false", onclick: async () => {
      const open = list.hidden;
      list.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      if (open && !list.childElementCount) {
        const rows = await db.rows(Q.issuesOfKind, { kind: k.kind });
        list.replaceChildren(...rows.map((r) => h("li", {}, h("button", {
          onclick: async () => { await showTrench(r.trench); await showUnit(r.units[0], { involved: r.units }); },
        }, r.detail))));
      }
    } }, h("span", { class: "k" }, k.kind.replaceAll("_", " ")),
         h("span", {}, h("span", { class: `sev ${k.severity}` }, k.severity), " ", h("b", {}, fmt(k.n))),
         h("span", { class: "why" }, KIND_TEXT[k.kind] || ""));
    return h("div", { class: "kind" }, btn, list);
  }));
}

for (const tab of document.querySelectorAll(".tabs button")) {
  tab.addEventListener("click", () => {
    for (const t of document.querySelectorAll(".tabs button")) t.setAttribute("aria-selected", String(t === tab));
    $("#tab-trenches").hidden = tab.dataset.tab !== "trenches";
    $("#tab-issues").hidden = tab.dataset.tab !== "issues";
  });
}

function renderLegend(periods) {
  $("#legend").replaceChildren(
    ...periods.map((p) => h("span", { title: `${year(p.start_year)} – ${year(p.end_year)} · ${p.units} units` },
      h("i", { style: { background: periodColor(p.name) } }), p.name)),
    h("span", {}, h("i", { style: { background: periodColor(null) } }), "undated"),
    h("span", {}, h("i", { class: "line" }), "above (later → earlier)"),
    h("span", {}, h("i", { class: "line contested" }), "contested claim"),
    h("span", {}, h("i", { class: "line same" }), "same as"),
    h("span", {}, h("i", { class: "flagged" }), "open issue"),
  );
}

// ------------------------------------------------------------------ matrix --

const svg = $("#matrix");
const wrap = $("#matrix-wrap");
let view = { x: 0, y: 0, w: 1000, h: 800 };
let graphBox = { w: 1000, h: 800 };

function setView(v) {
  view = v;
  svg.setAttribute("viewBox", `${v.x} ${v.y} ${v.w} ${v.h}`);
}

function fit() {
  const r = wrap.getBoundingClientRect();
  const pad = 40;
  const scale = Math.max(graphBox.w / Math.max(r.width - pad, 100), graphBox.h / Math.max(r.height - pad, 100), 0.8);
  const w = r.width * scale, hgt = r.height * scale;
  setView({ x: (graphBox.w - w) / 2, y: (graphBox.h - hgt) / 2, w, h: hgt });
}
$("#fit").addEventListener("click", fit);

// Big trenches open at a readable zoom (about 1 drawing unit per pixel),
// centred on the selected unit or on the top of the matrix. Small ones fit.
const READABLE = 1.05;
function fitScale() {
  const r = wrap.getBoundingClientRect();
  return Math.max(graphBox.w / Math.max(r.width - 40, 100), graphBox.h / Math.max(r.height - 40, 100), 0.8);
}
function frame(uid) {
  if (fitScale() <= 1.5) { fit(); return; }
  const r = wrap.getBoundingClientRect();
  const w = r.width * READABLE, hgt = r.height * READABLE;
  const p = uid && positions.get(uid);
  const cx = p ? p.x : graphBox.w / 2;
  const y = p ? p.y - hgt / 2 : -10;
  setView({ x: cx - w / 2, y, w, h: hgt });
}

wrap.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = wrap.getBoundingClientRect();
  const k = Math.exp(e.deltaY * 0.0015);
  const mx = view.x + ((e.clientX - r.left) / r.width) * view.w;
  const my = view.y + ((e.clientY - r.top) / r.height) * view.h;
  const w = Math.min(Math.max(view.w * k, 150), graphBox.w * 6 + 2000);
  const hgt = (w / view.w) * view.h;
  setView({ x: mx - ((mx - view.x) * w) / view.w, y: my - ((my - view.y) * hgt) / view.h, w, h: hgt });
}, { passive: false });

let drag = null;
wrap.addEventListener("pointerdown", (e) => {
  if (e.target.closest(".node")) return;
  drag = { x: e.clientX, y: e.clientY, v: { ...view } };
  wrap.classList.add("dragging");
  wrap.setPointerCapture(e.pointerId);
});
wrap.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const r = wrap.getBoundingClientRect();
  setView({ ...drag.v, x: drag.v.x - ((e.clientX - drag.x) / r.width) * view.w, y: drag.v.y - ((e.clientY - drag.y) / r.height) * view.h });
});
const endDrag = () => { drag = null; wrap.classList.remove("dragging"); };
wrap.addEventListener("pointerup", endDrag);
wrap.addEventListener("pointercancel", endDrag);
$("#show-implied").addEventListener("change", () => drawMatrix());

async function showTrench(uid) {
  if (state.trench === uid && state.units.size) return;
  state.trench = uid;
  state.unit = null;
  renderTrenches();
  const t = state.trenches.find((t) => t.uid === uid);
  $("#matrix-title").textContent = t ? t.name.replace("/", " · ") : uid;
  const [units, edges] = await Promise.all([
    db.run(Q.matrixUnits, { trench: uid }),
    db.run(Q.matrixEdges, { trench: uid }),
  ]);
  if (units.error || edges.error) { $("#matrix-empty").textContent = units.error || edges.error; return; }
  state.units = new Map(units.rows.map((u) => [u.uid, u]));
  state.edges = edges.rows;
  const contested = edges.rows.filter((e) => e.type === "CONTESTED").length;
  const flagged = units.rows.filter((u) => u.flagged).length;
  $("#matrix-meta").textContent =
    `${fmt(units.rows.length)} units · ${fmt(edges.rows.filter((e) => e.type === "ABOVE").length)} relations`
    + (contested ? ` · ${contested} contested` : "") + (flagged ? ` · ${flagged} units with open issues` : "")
    + ` · two queries, ${Math.round(units.ms + edges.ms)} ms`;
  $("#matrix-cypher").onclick = () => openConsole(Q.matrixEdges, { trench: uid });
  drawMatrix();
  frame(null);
  history.replaceState(null, "", `#${encodeURIComponent(t ? t.name : uid)}`);
  showPlaceholder();
}

function showPlaceholder() {
  $("#detail").replaceChildren(h("div", { class: "placeholder" },
    h("h3", {}, "Pick a unit"),
    h("p", {}, "Click a box in the matrix. Everything shown here comes from Cypher queries against the graph running in this page.")));
}

let positions = new Map();

function drawMatrix() {
  const showImplied = $("#show-implied").checked;
  const units = [...state.units.values()];
  const L = layout(units, state.edges);
  positions = L.pos;
  graphBox = { w: L.width, h: L.height };
  $("#matrix-empty").hidden = units.length > 0;
  $("#matrix-empty").textContent = units.length ? "" : "No units";

  const gEdges = s("g");
  for (const e of state.edges) {
    const a = L.pos.get(e.a), b = L.pos.get(e.b);
    if (!a || !b) continue;
    let cls = "edge";
    if (e.type === "ABOVE" && e.redundant) { if (!showImplied) continue; cls += " implied"; }
    if (e.type === "CONTESTED") cls += " contested";
    if (e.type === "REJECTED") cls += " rejected";
    if (e.type === "SAME_AS") cls += " same";
    let d;
    if (e.type === "SAME_AS" || Math.abs(a.y - b.y) < 1) {
      const dir = a.x < b.x ? 1 : -1;
      const ax = a.x + dir * L.nodeW / 2, bx = b.x - dir * L.nodeW / 2;
      d = `M${ax},${a.y} C${ax + dir * 30},${a.y - 28} ${bx - dir * 30},${b.y - 28} ${bx},${b.y}`;
    } else if (a.y < b.y) {
      const y1 = a.y + L.nodeH / 2, y2 = b.y - L.nodeH / 2, my = (y1 + y2) / 2;
      d = e.type === "CONTESTED"
        ? `M${a.x},${y1} C${a.x + 26},${my} ${b.x + 26},${my} ${b.x},${y2}`
        : `M${a.x},${y1} C${a.x},${my} ${b.x},${my} ${b.x},${y2}`;
    } else {
      // a claim pointing upward: contested by definition
      const y1 = a.y - L.nodeH / 2, y2 = b.y + L.nodeH / 2, my = (y1 + y2) / 2;
      d = `M${a.x},${y1} C${a.x - 40},${my} ${b.x - 40},${my} ${b.x},${y2}`;
    }
    gEdges.append(s("path", { d, class: cls, "data-a": e.a, "data-b": e.b }));
  }

  const gRows = s("g");
  for (const [i, y] of L.rowYs.entries()) {
    gRows.append(s("text", { x: 4, y: y + 3, class: "rowlabel" }, L.rowNames[i]));
  }

  const gNodes = s("g");
  for (const u of units) {
    const p = L.pos.get(u.uid);
    if (!p) continue;
    const period = u.periods[0] || null;
    const g = s("g", {
      class: `node${u.flagged ? " flagged" : ""}`, "data-uid": u.uid,
      transform: `translate(${p.x - L.nodeW / 2},${p.y - L.nodeH / 2})`, tabindex: 0, role: "button",
      "aria-label": `${u.label}${period ? ", " + period : ""}${u.flagged ? ", has open issues" : ""}`,
    });
    g.append(
      s("rect", { class: "box", width: L.nodeW, height: L.nodeH, rx: 3 }),
      s("rect", { class: "band", width: 6, height: L.nodeH - 2, x: 1, y: 1, rx: 2, fill: periodColor(period) }),
      s("rect", { class: "band tint", width: L.nodeW - 8, height: L.nodeH - 2, x: 7, y: 1, fill: periodColor(period) }),
      s("text", { x: L.nodeW / 2 + 3, y: L.nodeH / 2 + 4, "text-anchor": "middle" }, u.label.replace(/^Locus /, "")),
      s("title", {}, `${u.label} · ${u.type || "unit"} · ${u.periods.join(" / ") || "undated"}`),
    );
    g.addEventListener("click", () => showUnit(u.uid));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showUnit(u.uid); } });
    gNodes.append(g);
  }
  svg.replaceChildren(gRows, gEdges, gNodes);
  if (state.unit) highlight(state.unit, state.reach);
}

function highlight(uid, reach) {
  const later = new Set(reach?.sealedBy || []);
  const earlier = new Set(reach?.seals || []);
  const involved = new Set(state.involved || []);
  const any = !!uid;
  for (const n of svg.querySelectorAll(".node")) {
    const id = n.dataset.uid;
    n.classList.toggle("selected", id === uid);
    n.classList.toggle("later", later.has(id));
    n.classList.toggle("earlier", earlier.has(id));
    n.classList.toggle("involved", involved.has(id) && id !== uid);
    n.classList.toggle("dim", any && id !== uid && !later.has(id) && !earlier.has(id) && !involved.has(id));
  }
  const on = new Set([uid, ...later, ...earlier, ...involved]);
  for (const e of svg.querySelectorAll(".edge")) {
    const touches = e.dataset.a === uid || e.dataset.b === uid;
    e.classList.toggle("hot", touches && !e.classList.contains("contested"));
    e.classList.toggle("dim", any && !(on.has(e.dataset.a) && on.has(e.dataset.b)));
  }
}

function centerOn(uid) {
  const p = positions.get(uid);
  if (!p) return;
  if (view.w / wrap.getBoundingClientRect().width > 1.5) { frame(uid); return; }
  if (p.x < view.x + 40 || p.x > view.x + view.w - 40 || p.y < view.y + 40 || p.y > view.y + view.h - 40) {
    setView({ ...view, x: p.x - view.w / 2, y: p.y - view.h / 2 });
  }
}

// ------------------------------------------------------------ unit detail --

function section(title, query, params, ...body) {
  return h("div", { class: "sec" },
    h("div", { class: "sec-h" }, h("h4", {}, title),
      query ? h("button", { class: "cypher-link", onclick: () => openConsole(query, params), title: "Show the Cypher behind this" }, "Cypher") : null),
    ...body);
}

const unitButton = (uid, label, cls = "") => h("button", {
  class: `unit-link ${cls}`, onclick: () => goTo({ uid }),
}, (label || state.units.get(uid)?.label || uid.slice(0, 8)).replace(/^Locus /, "L "));

const recordName = (uid) => state.units.get(uid)?.label || `record ${uid.slice(0, 8)}`;

async function showUnit(uid, { keepView = false, involved = null } = {}) {
  state.unit = uid;
  if (!keepView) state.involved = involved || [];
  const p = { uid };
  const [unitRes, nb, iss, tpq, taq, seals, sealedBy, finds] = await Promise.all([
    db.run(Q.unit, p), db.run(Q.neighbours, p), db.run(Q.unitIssues, p),
    db.run(Q.tpq, p), db.run(Q.taq, p), db.run(Q.seals, p), db.run(Q.sealedBy, p),
    state.findsReady ? db.run(Q.finds, p) : Promise.resolve(null),
  ]);
  if (state.unit !== uid) return;
  const u = unitRes.rows[0];
  if (!u) return;
  state.reach = { seals: seals.rows[0]?.units || [], sealedBy: sealedBy.rows[0]?.units || [] };
  highlight(uid, state.reach);
  if (!keepView) centerOn(uid);
  history.replaceState(null, "", `#${encodeURIComponent(u.path)}`);
  const totalMs = [unitRes, nb, iss, tpq, taq, seals, sealedBy, finds].filter(Boolean).reduce((n, r) => n + r.ms, 0);

  const group = state.trenches.find((t) => t.uid === u.trench);
  const head = h("div", {},
    h("div", { class: "crumbs" }, u.path.includes("/") ? u.path.split("/").slice(0, -1).join(" · ") : (group ? group.name : "")),
    h("h3", {}, u.label),
    h("div", { class: "facts" }, [
      u.type || "unit",
      u.depth !== null && u.depth !== undefined ? `row ${u.depth} of the site matrix` : null,
      u.excavated_from ? `dug ${u.excavated_from}${u.excavated_to && u.excavated_to !== u.excavated_from ? " – " + u.excavated_to : ""}` : null,
    ].filter(Boolean).join(" · ")),
    h("div", { class: "chips" }, u.periods.length
      ? u.periods.map((pp) => h("span", { class: "chip" }, h("i", { style: { background: periodColor(pp.name) } }), pp.name,
          h("small", {}, [pp.start_year !== null && pp.start_year !== undefined ? `${year(pp.start_year)}–${year(pp.end_year)}` : null, pp.basis]
            .filter(Boolean).join(" · "))))
      : h("span", { class: "chip" }, h("i", { style: { background: periodColor(null) } }), u.period_label || "undated")),
  );

  const issuesSec = iss.rows.length ? section("Open issues", Q.unitIssues, p,
    ...iss.rows.map((i) => h("div", { class: `issue-card ${i.severity}` }, h("b", {}, i.kind.replaceAll("_", " ")), i.detail))) : null;

  // date bracket
  const lo = tpq.rows[0], hi = taq.rows[0];
  const problems = [], tight = [];
  const has = (v) => v !== null && v !== undefined;
  if (lo && has(u.end_year)) {
    if (u.end_year < lo.year) problems.push(`its recorded date ends before ${lo.label}, which it seals, begins`);
    else if (u.end_year === lo.year) tight.push(`it can only date to ${year(lo.year)}, the last year of its recorded range, when ${lo.label} below begins`);
  }
  if (hi && has(u.start_year)) {
    if (u.start_year > hi.year) problems.push(`its recorded date begins after ${hi.label}, which seals it, ends`);
    else if (u.start_year === hi.year) tight.push(`it can only date to ${year(hi.year)}, the first year of its recorded range, when ${hi.label} above ends`);
  }
  const bracket = section("Date bracket", Q.tpq, p,
    h("dl", { class: "bracket" },
      h("dt", {}, "Not earlier than"), h("dd", {}, lo ? [year(lo.year), " · ", unitButton(lo.uid, lo.label), " below, ", lo.period] : h("span", { class: "muted" }, "nothing dated below")),
      h("dt", {}, "Not later than"), h("dd", {}, hi ? [year(hi.year), " · ", unitButton(hi.uid, hi.label), " above, ", hi.period] : h("span", { class: "muted" }, "nothing dated above")),
      h("dt", {}, "Recorded"), h("dd", {}, u.start_year !== null && u.start_year !== undefined ? `${year(u.start_year)} – ${year(u.end_year)}` : "no date"),
    ),
    (lo || hi) && u.start_year !== null && u.start_year !== undefined
      ? h("div", { class: `verdict ${problems.length ? "bad" : tight.length ? "warn" : "ok"}` },
          problems.length ? `The stratigraphy disagrees: ${problems.join("; ")}.`
          : tight.length ? `Only just: ${tight.join("; ")}. Residual material would explain it.`
          : "The recorded date fits the stratigraphy.")
      : null);

  // relations
  const groups = { later: [], earlier: [], contested: [], same: [], within: [], abuts: [], rejected: [] };
  const claims = [];
  for (const r of nb.rows) {
    if (r.type === "ABOVE") (r.outgoing ? groups.earlier : groups.later).push(r);
    else if (r.type === "CONTESTED") { groups.contested.push(r); claims.push(r); }
    else if (r.type === "SAME_AS") groups.same.push(r);
    else if (r.type === "WITHIN") groups.within.push(r);
    else if (r.type === "ABUTS") groups.abuts.push(r);
    else if (r.type === "REJECTED") groups.rejected.push(r);
  }
  const relRow = (label, rows, cls) => rows.length
    ? [h("dt", {}, label), h("dd", {}, rows.map((r) => unitButton(r.uid, r.label, cls)))] : [];
  const rels = section("Relations", Q.neighbours, p,
    h("dl", { class: "rels" },
      ...relRow("Above (later)", groups.later), ...relRow("Below (earlier)", groups.earlier),
      ...relRow("Contested", groups.contested, "contested"), ...relRow("Same as", groups.same),
      ...relRow("Within", groups.within), ...relRow("Abuts", groups.abuts), ...relRow("Rejected", groups.rejected)),
    claims.length ? h("ul", { class: "claims" }, claims.map((c) => h("li", {},
      h("b", {}, c.outgoing ? `${u.label} ${c.verbs.join("/")} ${c.label}` : `${c.label} ${c.verbs.join("/")} ${u.label}`),
      ` (set aside) — stated by ${c.stated_by.map(recordName).join(", ")}`))) : null,
    h("p", { class: "muted" },
      `Seals ${fmt(state.reach.seals.length)} units (outlined brown) · sealed by ${fmt(state.reach.sealedBy.length)} (outlined blue).`),
  );

  const findsSec = section("Finds", Q.finds, p, finds
    ? (finds.rows.length
      ? h("table", { class: "finds" }, h("tbody", {}, finds.rows.map((f) => h("tr", {},
          h("td", {}, f.category || "uncategorised", f.taxa.length ? h("div", { class: "taxa" }, f.taxa.slice(0, 6).join(", ") + (f.taxa.length > 6 ? "…" : "")) : null),
          h("td", { class: "n" }, fmt(f.n))))))
      : h("p", { class: "muted" }, "No finds recorded from this unit."))
    : h("p", { class: "muted" }, "Finds are still loading into the graph…"));

  const note = u.description ? section("Excavator's note", null, null, h("blockquote", { class: "note" }, u.description)) : null;
  const src = section("Record", null, null, h("div", { class: "src" },
    safeUrl(u.uri) ? h("a", { href: safeUrl(u.uri), target: "_blank", rel: "noopener" },
      /opencontext\.org/.test(u.uri) ? "Open Context record ↗" : "Source record ↗") : null,
    safeUrl(u.citation) ? h("a", { href: safeUrl(u.citation), target: "_blank", rel: "noopener" }, u.citation.replace("https://", "")) : null,
    h("span", { class: "muted" }, `${u.path} · answered by ${8 - (finds ? 0 : 1)} Cypher queries in ${Math.round(totalMs)} ms`)));

  $("#detail").replaceChildren(h("div", { class: "unit" }, head, issuesSec, note, bracket, rels, findsSec, src));
  $("#detail").scrollTop = 0;
}

// ----------------------------------------------------------------- console --

const consoleEl = $("#console");
const editor = $("#cypher");
const paramsEl = $("#params");

function toggleConsole(open = consoleEl.hidden) {
  consoleEl.hidden = !open;
  $("#console-toggle").setAttribute("aria-pressed", String(open));
  if (open) editor.focus();
  requestAnimationFrame(() => frame(state.unit));
}
$("#console-toggle").addEventListener("click", () => toggleConsole());
$("#console-close").addEventListener("click", () => toggleConsole(false));
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.key === "`") { e.preventDefault(); toggleConsole(); }
});

function openConsole(query, params) {
  editor.value = query.trim();
  paramsEl.value = params ? JSON.stringify(params) : "";
  toggleConsole(true);
  runConsole();
}

function fillExamples() {
  const sel = $("#examples");
  for (const [i, [label]] of EXAMPLES.entries()) sel.append(h("option", { value: i }, label));
  sel.addEventListener("change", () => {
    if (sel.value === "") return;
    editor.value = EXAMPLES[Number(sel.value)][1];
    paramsEl.value = "";
    sel.value = "";
    runConsole();
  });
}

const cell = (v) => {
  if (v === null || v === undefined) return h("td", { class: "json" }, "null");
  if (typeof v === "object") return h("td", { class: "json" }, JSON.stringify(v));
  return h("td", {}, String(v));
};

async function runConsole() {
  const out = $("#results");
  let params = null;
  if (paramsEl.value.trim()) {
    try { params = JSON.parse(paramsEl.value); } catch (e) {
      out.replaceChildren(h("div", { class: "err" }, `Parameters are not valid JSON: ${e.message}`));
      return;
    }
  }
  $("#console-meta").textContent = "running…";
  const res = await db.run(editor.value, params);
  if (res.error) {
    $("#console-meta").textContent = "";
    out.replaceChildren(h("div", { class: "err" }, res.error));
    return;
  }
  const rows = res.rows || [];
  const cols = res.columns?.length ? res.columns : Object.keys(rows[0] || {});
  const shown = rows.slice(0, 500);
  $("#console-meta").textContent = `${fmt(rows.length)} rows · ${res.ms.toFixed(1)} ms in Relate (WASM), in this tab`
    + (rows.length > shown.length ? " · first 500 shown" : "");
  out.replaceChildren(rows.length
    ? h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)))),
        h("tbody", {}, shown.map((r) => h("tr", {}, cols.map((c) => cell(r[c]))))))
    : h("div", { class: "none" }, "No rows."));
}
$("#run").addEventListener("click", runConsole);
editor.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runConsole(); }
});

window.addEventListener("resize", () => frame(state.unit));
window.addEventListener("hashchange", () => { const t = parseHash(); if (t && db) goTo(t); });

// ------------------------------------------------------------------ WebMCP --

let toastTimer = null;
function notify(text) {
  const el = $("#agent-toast");
  el.replaceChildren(h("b", {}, "Agent"), " ", text);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

async function startWebMCP() {
  const reg = await registerTools({
    db: () => db,
    state: () => state,
    source: () => state.source,
    catalogue: () => catalogue,
    goTo, showTrench, openBuiltin, openUrl, openConsole, notify,
  });
  const badge = $("#webmcp");
  if (reg && reg.count) {
    badge.hidden = false;
    badge.textContent = `WebMCP · ${reg.count} tools`;
    badge.title = `This page offers ${reg.count} tools to AI agents in your browser (WebMCP):\n${reg.names.join("\n")}`;
  }
}

boot();
startWebMCP().catch((e) => console.warn("WebMCP:", e));
