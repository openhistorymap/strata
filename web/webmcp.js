// WebMCP: the viewer offers its graph to AI agents running in the browser.
//
// The W3C draft (webmachinelearning.github.io/webmcp) defines
// document.modelContext.registerTool(tool, {signal}); Chrome 146 and the
// MCP-B polyfill expose navigator.modelContext. Either works here. Tools answer
// from the Relate (WASM) graph in this tab, and a few drive the page itself.
// Results use the MCP shape ({content: [{type: "text", text}]}, plus
// structuredContent), which is plain JSON as the spec requires.

const WRITE = /\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b/i;
const STRINGS = /'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`[^`]*`/g;
export const isWrite = (q) => WRITE.test(q.replace(STRINGS, "''"));

export function modelContext() {
  return (typeof document !== "undefined" && document.modelContext)
      || (typeof navigator !== "undefined" && navigator.modelContext)
      || null;
}

const ok = (value) => ({ content: [{ type: "text", text: JSON.stringify(value, null, 1) }], structuredContent: value });
const fail = (message) => ({ content: [{ type: "text", text: message }], isError: true });

/**
 * The viewer's tools, shared by WebMCP and the in-page assistant (assistant.js).
 * `app` gives access to the page:
 *   db()          the current GraphClient
 *   source()      what is loaded ({kind, name|url|id, dataset})
 *   catalogue()   built-in datasets
 *   goTo({path|uid|trenchName}), showTrench(uid), openBuiltin(id), openUrl(url),
 *   openConsole(cypher, params), notify(text)
 * Each tool's execute() never throws: errors come back as an isError result.
 */
export function buildTools(app) {
  const rows = async (cypher, params) => app.db().rows(cypher, params);

  async function resolveUnit(ref) {
    const found = await rows(`
      MATCH (c:Context)-[:IN_TRENCH|IN_SITE]->(g)
      WHERE c.uid = $ref OR c.path = $ref OR c.path ENDS WITH '/' + $ref
         OR (c.path IS NULL AND c.label = $ref)
      RETURN c.uid AS uid, coalesce(c.path, c.label) AS path, coalesce(g.uid, g.id) AS trench
      LIMIT 20`, { ref: String(ref || "").trim() });
    if (!found.length) throw new Error(`No unit matches "${ref}". Use a full path such as "Area F/Trench 1/Locus 1032".`);
    const current = app.state().trench;
    const inTrench = found.filter((f) => f.trench === current);
    if (found.length > 1 && inTrench.length !== 1) {
      throw new Error(`"${ref}" matches ${found.length} units: ${found.slice(0, 8).map((f) => f.path).join("; ")}. Use the full path.`);
    }
    return inTrench.length === 1 ? inTrench[0] : found[0];
  }

  const tools = [
    {
      name: "strata_dataset",
      title: "What is loaded",
      description: "Which excavation dataset this page has loaded into its in-browser Relate graph, where it came from (built-in, a URL, or a local file), and its size.",
      inputSchema: { type: "object", properties: {} },
      annotations: { readOnlyHint: true },
      async execute() {
        const [site] = await rows(`MATCH (s:Site) RETURN s.id AS id, s.name AS name, s.source AS source,
          s.license_label AS license, s.wikidata AS wikidata,
          COUNT { MATCH (c:Context) WHERE c.site = s.id } AS units`);
        const [counts] = await rows(`MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS relationships`);
        const src = app.source();
        return ok({
          source: src.kind === "local" ? { kind: "local file", name: src.name, bytes: src.size, note: "read in the tab, never uploaded" }
                : src.kind === "url" ? { kind: "url", url: src.url } : { kind: "built-in", id: src.id },
          site, ...counts,
          fully_loaded: app.state().findsReady,
          available: app.catalogue().map((d) => ({ id: d.id, name: d.name })),
        });
      },
    },
    {
      name: "strata_trenches",
      title: "List trenches",
      description: "Every trench (or 'Units without a trench') with its number of units, open issues and matrix rows.",
      inputSchema: { type: "object", properties: {} },
      annotations: { readOnlyHint: true },
      async execute() {
        return ok(app.state().trenches.map(({ uid, name, units, issues, rows: r }) => ({ id: uid, name, units, issues, rows: r })));
      },
    },
    {
      name: "strata_issues",
      title: "Open issues",
      description: "Problems the checks found in the excavation record (contradictory records, loops, period inversions…), each with the units involved.",
      inputSchema: {
        type: "object",
        properties: {
          kind: { type: "string", description: "Only this kind, e.g. period_inversion, self_contradiction, cycle." },
          limit: { type: "integer", minimum: 1, maximum: 200, default: 30 },
        },
      },
      annotations: { readOnlyHint: true },
      async execute({ kind = null, limit = 30 } = {}) {
        return ok(await rows(`
          MATCH (i:Issue {status: 'open'}) WHERE $kind IS NULL OR i.kind = $kind
          RETURN i.id AS id, i.kind AS kind, i.severity AS severity, i.detail AS detail,
                 [(i)-[:INVOLVES]->(c) | coalesce(c.path, c.label)] AS units
          ORDER BY CASE i.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, kind, id
          LIMIT $limit`, { kind, limit: Math.min(Number(limit) || 30, 200) }));
      },
    },
    {
      name: "strata_describe",
      title: "Describe a unit",
      description: "One stratigraphic unit: its record and excavator's note, period, matrix row, what lies directly above and below, contested claims, open issues, date bracket and finds.",
      inputSchema: {
        type: "object",
        properties: { unit: { type: "string", description: 'Full path, e.g. "Area F/Trench 1/Locus 1032", or a label in the trench on screen.' } },
        required: ["unit"],
      },
      annotations: { readOnlyHint: true },
      async execute({ unit }) {
        const u = await resolveUnit(unit);
        const p = { uid: u.uid };
        const [rec] = await rows(`MATCH (c:Context {uid: $uid}) OPTIONAL MATCH (c)-[:DATED_TO]->(p:Period)
          RETURN coalesce(c.path, c.label) AS path, c.type AS type, c.depth AS matrix_row,
                 c.description AS note, c.period_label AS period, c.start_year AS start_year, c.end_year AS end_year,
                 c.uri AS uri, collect(p.name) AS periods`, p);
        const rels = await rows(`MATCH (c:Context {uid: $uid})-[r:ABOVE|CONTESTED|REJECTED|SAME_AS|WITHIN|ABUTS]-(o:Context)
          RETURN type(r) AS type, startNode(r) = c AS from_this, coalesce(o.path, o.label) AS other, r.verbs AS verbs,
                 [s IN coalesce(r.stated_by, []) | s] AS stated_by`, p);
        const issues = await rows(`MATCH (:Context {uid: $uid})<-[:INVOLVES]-(i:Issue {status: 'open'})
          RETURN i.kind AS kind, i.severity AS severity, i.detail AS detail`, p);
        const [lo] = await rows(`MATCH (:Context {uid: $uid})-[:ABOVE*]->(d:Context) WHERE d.start_year IS NOT NULL
          WITH DISTINCT d ORDER BY d.start_year DESC LIMIT 1 RETURN d.start_year AS year, coalesce(d.path, d.label) AS from_unit`, p);
        const [hi] = await rows(`MATCH (u:Context)-[:ABOVE*]->(:Context {uid: $uid}) WHERE u.end_year IS NOT NULL
          WITH DISTINCT u ORDER BY u.end_year ASC LIMIT 1 RETURN u.end_year AS year, coalesce(u.path, u.label) AS from_unit`, p);
        const finds = await rows(`MATCH (:Context {uid: $uid})<-[:FROM]-(f:Find) OPTIONAL MATCH (f)-[:IDENTIFIED_AS]->(t:Taxon)
          RETURN f.category AS category, count(f) AS n, collect(DISTINCT t.name) AS taxa ORDER BY n DESC`, p);
        const pick = (type, dir) => rels.filter((r) => r.type === type && (dir === undefined || r.from_this === dir)).map((r) => r.other);
        return ok({
          ...rec,
          directly_above_later: pick("ABOVE", false),
          directly_below_earlier: pick("ABOVE", true),
          same_as: pick("SAME_AS"),
          contested_claims: rels.filter((r) => r.type === "CONTESTED").map((r) => ({
            claim: r.from_this ? `${rec.path} ${r.verbs.join("/")} ${r.other}` : `${r.other} ${r.verbs.join("/")} ${rec.path}`,
            records: r.stated_by.length,
          })),
          open_issues: issues,
          date_bracket: { not_earlier_than: lo || null, not_later_than: hi || null, years: "astronomical: -3099 is 3100 BCE" },
          finds,
          note_on_years: "start_year/end_year are astronomical years: -3099 is 3100 BCE",
        });
      },
    },
    {
      name: "strata_relation",
      title: "How two units relate",
      description: "Whether unit a is earlier, later or unrelated to unit b, with the chain of recorded relations that proves it, and any contested direct claims between them.",
      inputSchema: {
        type: "object",
        properties: { a: { type: "string" }, b: { type: "string" } },
        required: ["a", "b"],
      },
      annotations: { readOnlyHint: true },
      async execute({ a, b }) {
        const [ua, ub] = [await resolveUnit(a), await resolveUnit(b)];
        const chain = async (x, y) => (await rows(`
          MATCH p = shortestPath((x:Context {uid: $x})-[:ABOVE*]->(y:Context {uid: $y}))
          RETURN [n IN nodes(p) | coalesce(n.path, n.label)] AS units, [r IN relationships(p) | r.verbs] AS verbs`,
          { x, y }))[0] || null;
        const down = await chain(ua.uid, ub.uid);
        const up = down ? null : await chain(ub.uid, ua.uid);
        const contested = await rows(`MATCH (x:Context {uid: $a})-[r:CONTESTED]-(y:Context {uid: $b})
          RETURN startNode(r) = x AS a_over_b, r.verbs AS verbs, size(r.stated_by) AS records`, { a: ua.uid, b: ub.uid });
        return ok({
          a: ua.path, b: ub.path,
          a_is: down ? "later (above)" : up ? "earlier (below)" : "unrelated in the matrix",
          chain: down || up,
          contested_direct_claims: contested,
        });
      },
    },
    {
      name: "strata_query",
      title: "Read-only Cypher",
      description: "Run a read-only Cypher query against the excavation graph in this tab. Model: (:Context)-[:IN_TRENCH]->(:Trench)-[:IN_SITE]->(:Site); (a:Context)-[:ABOVE]->(b) means a is later; :CONTESTED is a claim set aside; (:Find)-[:FROM]->(:Context); (:Context)-[:DATED_TO]->(:Period); (:Issue)-[:INVOLVES]->(:Context).",
      inputSchema: {
        type: "object",
        properties: {
          cypher: { type: "string" },
          params: { type: "object", description: "Query parameters." },
          limit: { type: "integer", minimum: 1, maximum: 500, default: 100 },
        },
        required: ["cypher"],
      },
      annotations: { readOnlyHint: true },
      async execute({ cypher, params = null, limit = 100 }) {
        if (isWrite(cypher)) return fail("This tool only reads; the graph in this page is not edited by agents.");
        const res = await app.db().run(cypher, params);
        if (res.error) return fail(res.error);
        const n = Math.min(Number(limit) || 100, 500);
        return ok({ columns: res.columns, rows: res.rows.slice(0, n), truncated: res.rows.length > n, ms: Math.round(res.ms) });
      },
    },
    {
      name: "strata_show_unit",
      title: "Show a unit on screen",
      description: "Open a unit in the viewer: its trench's Harris matrix, with the unit selected and everything it seals or is sealed by outlined, and its record in the side panel.",
      inputSchema: { type: "object", properties: { unit: { type: "string" } }, required: ["unit"] },
      async execute({ unit }) {
        const u = await resolveUnit(unit);
        await app.goTo({ uid: u.uid });
        return ok({ shown: u.path });
      },
    },
    {
      name: "strata_show_query",
      title: "Run a query in the console",
      description: "Put a read-only Cypher query in the viewer's console, run it, and show the result table to the user.",
      inputSchema: { type: "object", properties: { cypher: { type: "string" }, params: { type: "object" } }, required: ["cypher"] },
      async execute({ cypher, params = null }) {
        if (isWrite(cypher)) return fail("Only read queries are shown.");
        app.openConsole(cypher, params);
        return ok({ shown: true });
      },
    },
    {
      name: "strata_open_dataset",
      title: "Open another dataset",
      description: "Replace the graph in this tab with a built-in dataset (by id) or a Strata export at an http(s) URL. The current view is lost.",
      inputSchema: {
        type: "object",
        properties: { dataset: { type: "string", description: "A built-in id, see strata_dataset." }, url: { type: "string" } },
      },
      annotations: { consequentialHint: true },
      async execute({ dataset = null, url = null } = {}) {
        if (url) { app.openUrl(url); return ok({ opening: url }); }
        if (dataset && app.catalogue().some((d) => d.id === dataset)) { app.openBuiltin(dataset); return ok({ opening: dataset }); }
        return fail(`Give a url, or one of: ${app.catalogue().map((d) => d.id).join(", ")}`);
      },
    },
  ];

  return tools.map((tool) => ({
    ...tool,
    async execute(input = {}, ...rest) {
      app.notify(`${tool.title}${input && (input.unit || input.a) ? ` · ${input.unit || `${input.a} ↔ ${input.b}`}` : ""}`);
      try { return await tool.execute(input || {}, ...rest); } catch (e) { return fail(String((e && e.message) || e)); }
    },
  }));
}

/** Offer the tools to AI agents in the browser. Returns {count, names} or null without WebMCP. */
export async function registerTools(app) {
  const mc = modelContext();
  if (!mc || typeof mc.registerTool !== "function") return null;
  const tools = buildTools(app);
  const controller = new AbortController();
  let count = 0;
  for (const wrapped of tools) {
    try {
      const handle = await mc.registerTool(wrapped, { signal: controller.signal });
      if (handle && typeof handle.unregister === "function") controller.signal.addEventListener("abort", () => handle.unregister());
      count++;
    } catch (e) {
      console.warn(`WebMCP: could not register ${wrapped.name}:`, e);
    }
  }
  return { count, names: tools.map((t) => t.name), unregister: () => controller.abort() };
}
