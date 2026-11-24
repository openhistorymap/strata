// harris-mcp from the browser: the Harris-matrix analysis tools of
// https://github.com/openhistorymap/harris-mcp, called over MCP Streamable HTTP.
//
// The page sends one matrix (the trench on screen, or the whole site) to the
// server as an HMDP JSON file with upload_matrix, then the model may call the
// read-only analysis tools on it. The matrix id is filled in here, so the model
// never has to carry it around.

export const HARRIS_URL = "https://harris.openhistorymap.org/mcp/http";

// Read-only tools that analyse one open matrix. Editing tools are left out:
// they would change only the server's copy, never the record in this page.
const READ_ONLY = [
  "summary", "validate", "phase_sequence", "anomalies", "topological_layers",
  "describe_context", "describe_phase", "get_context", "list_contexts", "count_contexts",
  "search_contexts", "neighbors", "ancestors", "descendants", "relation", "path", "between",
  "contemporaries", "phases", "periods", "phase_contexts", "boundary_contexts",
];

let nextId = 1;

/** A minimal MCP client over Streamable HTTP (JSON or SSE answers). */
export class McpHttp {
  constructor(url) { this.url = url; this.session = null; this.ready = null; }

  async rpc(method, params, { notify = false } = {}) {
    const body = { jsonrpc: "2.0", method, ...(params ? { params } : {}), ...(notify ? {} : { id: nextId++ }) };
    const headers = { "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18" };
    if (this.session) headers["mcp-session-id"] = this.session;
    const res = await fetch(this.url, { method: "POST", headers, body: JSON.stringify(body) });
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.session = sid;
    if (notify) return null;
    if (!res.ok) throw new Error(`harris-mcp answered ${res.status}`);
    const type = res.headers.get("content-type") || "";
    let msg;
    if (type.includes("text/event-stream")) {
      const text = await res.text();
      const data = text.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim());
      msg = data.map((d) => { try { return JSON.parse(d); } catch { return null; } }).find((m) => m && m.id === body.id);
    } else {
      msg = await res.json();
    }
    if (!msg) throw new Error("harris-mcp sent no answer");
    if (msg.error) throw new Error(msg.error.message || JSON.stringify(msg.error));
    return msg.result;
  }

  connect() {
    this.ready ??= (async () => {
      const init = await this.rpc("initialize", {
        protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "strata-viewer", version: "1" },
      });
      await this.rpc("notifications/initialized", null, { notify: true }).catch(() => {});
      const { tools } = await this.rpc("tools/list", {});
      return { server: init.serverInfo, tools };
    })().catch((e) => { this.ready = null; throw e; });
    return this.ready;
  }

  async call(name, args) {
    await this.connect();
    return this.rpc("tools/call", { name, arguments: args });
  }
}

/** The matrix the page would send: Strata's graph as an HMDP file (see harris_mcp/formats/hmdp.py). */
async function hmdp(app, scope, trenchRef) {
  const rows = (q, p) => app.db().rows(q, p);
  let trench = null;
  if (scope === "trench") {
    const ref = trenchRef || app.state().trench;
    const [t] = await rows(`MATCH (t:Trench) WHERE t.uid = $ref OR t.name = $ref RETURN t.uid AS uid, t.name AS name LIMIT 1`, { ref });
    if (!t) throw new Error(`No trench "${ref}". The trenches are listed by strata_trenches.`);
    trench = t;
  }
  const where = trench ? "c.uid IN uids" : "true";
  const params = trench ? { t: trench.uid } : {};
  const pre = trench ? "MATCH (x:Context)-[:IN_TRENCH]->(:Trench {uid: $t}) WITH collect(x.uid) AS uids " : "WITH [] AS uids ";
  const contexts = await rows(`${pre}
    MATCH (c:Context) WHERE ${where}
    OPTIONAL MATCH (c)-[:IN_TRENCH]->(t:Trench)
    OPTIONAL MATCH (c)-[:DATED_TO]->(p:Period)
    WITH c, t, collect(p.name)[0] AS dated
    RETURN coalesce(c.path, c.label) AS id, c.type AS type, coalesce(c.period_label, dated) AS period,
           coalesce(c.phase, c.period_label, dated) AS phase, t.name AS grp, c.description AS description`, params);
  const above = await rows(`${pre}
    MATCH (a:Context)-[r:ABOVE]->(b:Context) WHERE ${where.replace("c.", "a.")} AND ${where.replace("c.", "b.")}
    RETURN coalesce(a.path, a.label) AS a, coalesce(b.path, b.label) AS b, r.verbs AS verbs`, params);
  const same = await rows(`${pre}
    MATCH (a:Context)-[:SAME_AS]->(b:Context) WHERE ${where.replace("c.", "a.")} AND ${where.replace("c.", "b.")}
    RETURN coalesce(a.path, a.label) AS a, coalesce(b.path, b.label) AS b`, params);
  const kind = (verbs = []) => (verbs.includes("cuts") ? "cuts" : verbs.includes("fills") ? "fills" : "above");
  const [site] = await rows(`MATCH (s:Site) RETURN s.id AS id, s.name AS name LIMIT 1`);
  const name = trench ? `${site?.name || "site"} · ${trench.name}` : (site?.name || "site");
  const doc = {
    name, profile: "harris-matrix-data-package-v1",
    contexts: contexts.map((c) => {
      const out = { id: String(c.id) };
      for (const [k, v] of [["type", c.type], ["period", c.period], ["phase", c.phase], ["group", c.grp], ["description", c.description]]) {
        if (v !== null && v !== undefined && v !== "") out[k] = String(v);
      }
      return out;
    }),
    relations: [
      ...above.map((r) => ({ a: String(r.a), b: String(r.b), kind: kind(r.verbs || []), attrs: { strata_verbs: r.verbs || [] } })),
      ...same.map((r) => ({ a: String(r.a), b: String(r.b), kind: "equal" })),
    ],
  };
  const slug = (site?.id || "site") + (trench ? `-${trench.name}` : "");
  return { name, file: `${slug.replace(/[^A-Za-z0-9._+()-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 100) || "matrix"}.hmdp.json`,
           text: JSON.stringify(doc), units: doc.contexts.length, relations: doc.relations.length };
}

const ok = (value) => ({ content: [{ type: "text", text: JSON.stringify(value, null, 1) }], structuredContent: value });
const fail = (message) => ({ content: [{ type: "text", text: message }], isError: true });

/**
 * harris-mcp tools for the assistant, in the same shape as webmcp.js's tools.
 * `onStatus(text)` reports what is happening (uploads, failures).
 */
export async function harrisTools(app, { url = HARRIS_URL, onStatus = () => {} } = {}) {
  const client = new McpHttp(url);
  const { server, tools } = await client.connect();
  let open = null;            // {source, scope, trench, matrix_id, name, units, relations, first_analysis}
  const sourceKey = () => { const s = app.source(); return `${s.kind}:${s.url || s.id || s.name}`; };

  async function ensure(scope = "trench", trench = null) {
    const want = scope === "trench" ? (trench || app.state().trench) : null;
    if (open && open.source === sourceKey() && open.scope === scope && (scope === "site" || open.trench === want)) return open;
    const m = await hmdp(app, scope, want);
    onStatus(`sending ${m.units} units to harris-mcp…`);
    const res = await client.call("upload_matrix", { files: { [m.file]: m.text }, name: m.name });
    if (res.isError) throw new Error(res.content?.map((c) => c.text).join(" ") || "upload failed");
    const s = res.structuredContent || JSON.parse(res.content[0].text);
    open = { source: sourceKey(), scope, trench: want, matrix_id: s.matrix_id, name: m.name, units: m.units, relations: m.relations, first_analysis: s };
    onStatus(`harris-mcp has ${m.name}`);
    return open;
  }
  // The matrix the other tools work on: the one opened last, if it still belongs to the dataset on screen.
  const current = () => (open && open.source === sourceKey() ? open : ensure("trench"));

  const strip = (schema) => {
    const s = JSON.parse(JSON.stringify(schema || { type: "object", properties: {} }));
    delete s.properties?.matrix_id;
    if (Array.isArray(s.required)) s.required = s.required.filter((r) => r !== "matrix_id");
    return s;
  };

  const out = [{
    name: "harris_open",
    title: "harris-mcp: open a matrix",
    description: "Send a matrix from this page to harris-mcp (the Harris-matrix analysis server) and get its first analysis: "
      + "structural validation, the phase order implied by the stratigraphy, and phase anomalies. scope 'trench' sends one trench "
      + "(the one on screen unless `trench` is given), 'site' sends every unit. Every other harris_* tool then works on that matrix; "
      + "they open the trench on screen by themselves if nothing was opened. Units are named by their Strata path. Phases are the "
      + "recorded phase, or the period when no phase was recorded.",
    inputSchema: { type: "object", properties: {
      scope: { type: "string", enum: ["trench", "site"], default: "trench" },
      trench: { type: "string", description: "Trench name or id, for scope 'trench'." },
    } },
    async execute({ scope = "trench", trench = null } = {}) {
      try {
        const o = await ensure(scope, trench);
        return ok({ matrix: o.name, units: o.units, relations: o.relations, analysis: o.first_analysis });
      } catch (e) { return fail(String(e.message || e)); }
    },
  }];

  for (const t of tools.filter((t) => READ_ONLY.includes(t.name))) {
    out.push({
      name: `harris_${t.name}`,
      title: `harris-mcp: ${t.name.replace(/_/g, " ")}`,
      description: `${(t.description || "").trim()}\n(harris-mcp, on the matrix opened with harris_open.)`,
      inputSchema: strip(t.inputSchema),
      async execute(args = {}) {
        try {
          const o = await current();
          return await client.call(t.name, { ...args, matrix_id: o.matrix_id });
        } catch (e) { return fail(String(e.message || e)); }
      },
    });
  }
  return { server, tools: out, opened: () => open };
}
