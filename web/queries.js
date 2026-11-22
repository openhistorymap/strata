// Every view in the viewer is one of these Cypher queries, run by Relate in
// the browser. The page shows each query next to its result ("Cypher" links).
//
// A matrix is drawn per group: a trench, or, for units recorded without a
// trench (most Harris-matrix files), the site itself. Units point at their
// group with IN_TRENCH or IN_SITE; a group's id is its uid (trench) or id (site).

export const Q = {
  site: `
MATCH (s:Site)
RETURN s.id AS id, s.name AS name, s.source AS source, s.source_uri AS source_uri,
       s.license_label AS license, s.wikidata AS wikidata, s.location AS location,
       COUNT { (s)<-[:IN_SITE]-(:Trench) } AS trenches,
       COUNT { MATCH (c:Context) WHERE c.site = s.id } AS units`,

  trenches: `
MATCH (c:Context)-[:IN_TRENCH|IN_SITE]->(g)
OPTIONAL MATCH (c)<-[:INVOLVES]-(i:Issue)
RETURN coalesce(g.uid, g.id) AS uid,
       CASE WHEN g.uid IS NULL THEN 'Units without a trench' ELSE g.name END AS name,
       count(DISTINCT c) AS units, count(DISTINCT i) AS issues, max(c.depth) AS rows
ORDER BY name`,

  issueKinds: `
MATCH (i:Issue {status: 'open'})
RETURN i.kind AS kind, i.severity AS severity, count(*) AS n
ORDER BY CASE i.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, n DESC`,

  issuesOfKind: `
MATCH (i:Issue {kind: $kind, status: 'open'})-[v:INVOLVES]->(c:Context)-[:IN_TRENCH|IN_SITE]->(g)
WITH i, g, c ORDER BY v.position
RETURN i.id AS id, i.detail AS detail, i.severity AS severity,
       coalesce(g.uid, g.id) AS trench, collect(c.uid) AS units
ORDER BY trench, id`,

  periods: `
MATCH (p:Period)<-[:DATED_TO]-(c:Context)
RETURN p.name AS name, p.start_year AS start_year, p.end_year AS end_year, count(c) AS units
ORDER BY start_year`,

  matrixUnits: `
MATCH (c:Context)-[:IN_TRENCH|IN_SITE]->(g) WHERE coalesce(g.uid, g.id) = $trench
OPTIONAL MATCH (c)-[:DATED_TO]->(p:Period)
RETURN c.uid AS uid, c.label AS label, c.type AS type, c.depth AS depth,
       collect(p.name) AS periods,
       EXISTS { MATCH (c)<-[:INVOLVES]-(:Issue {status: 'open'}) } AS flagged`,

  matrixEdges: `
MATCH (a:Context)-[:IN_TRENCH|IN_SITE]->(g) WHERE coalesce(g.uid, g.id) = $trench
MATCH (a)-[r:ABOVE|CONTESTED|REJECTED|SAME_AS]->(b:Context)
RETURN a.uid AS a, b.uid AS b, type(r) AS type, coalesce(r.redundant, false) AS redundant`,

  unit: `
MATCH (c:Context {uid: $uid})-[:IN_TRENCH|IN_SITE]->(g)
OPTIONAL MATCH (c)-[d:DATED_TO]->(p:Period)
RETURN c.uid AS uid, coalesce(c.path, c.label) AS path, c.label AS label, c.type AS type, c.depth AS depth,
       c.description AS description, c.uri AS uri, c.citation AS citation,
       c.period_label AS period_label, c.start_year AS start_year, c.end_year AS end_year,
       c.excavated_from AS excavated_from, c.excavated_to AS excavated_to,
       c.top_depth AS top, c.bottom_depth AS bottom, c.recorded_by AS recorded_by,
       coalesce(g.uid, g.id) AS trench, collect(p {.name, .start_year, .end_year, basis: d.basis}) AS periods`,

  neighbours: `
MATCH (c:Context {uid: $uid})-[r:ABOVE|CONTESTED|REJECTED|SAME_AS|WITHIN|ABUTS]-(o:Context)
RETURN type(r) AS type, startNode(r) = c AS outgoing, o.uid AS uid, o.label AS label,
       r.verbs AS verbs, r.stated_by AS stated_by, coalesce(r.redundant, false) AS redundant
ORDER BY type, outgoing DESC, label`,

  unitIssues: `
MATCH (c:Context {uid: $uid})<-[:INVOLVES]-(i:Issue {status: 'open'})
RETURN i.id AS id, i.kind AS kind, i.severity AS severity, i.detail AS detail`,

  seals: `
MATCH (:Context {uid: $uid})-[:ABOVE*]->(e:Context)
RETURN collect(DISTINCT e.uid) AS units`,

  sealedBy: `
MATCH (u:Context)-[:ABOVE*]->(:Context {uid: $uid})
RETURN collect(DISTINCT u.uid) AS units`,

  tpq: `
MATCH (:Context {uid: $uid})-[:ABOVE*]->(d:Context) WHERE d.start_year IS NOT NULL
WITH DISTINCT d ORDER BY d.start_year DESC LIMIT 1
RETURN d.start_year AS year, d.uid AS uid, d.label AS label, d.period_label AS period`,

  taq: `
MATCH (u:Context)-[:ABOVE*]->(:Context {uid: $uid}) WHERE u.end_year IS NOT NULL
WITH DISTINCT u ORDER BY u.end_year ASC LIMIT 1
RETURN u.end_year AS year, u.uid AS uid, u.label AS label, u.period_label AS period`,

  finds: `
MATCH (:Context {uid: $uid})<-[:FROM]-(f:Find)
OPTIONAL MATCH (f)-[:IDENTIFIED_AS]->(t:Taxon)
RETURN f.category AS category, count(f) AS n,
       collect(DISTINCT t.name) AS taxa
ORDER BY n DESC`,

  findsLoaded: `MATCH (f:Find) RETURN count(f) AS n`,

  byPath: `
MATCH (c:Context)-[:IN_TRENCH|IN_SITE]->(g) WHERE c.path = $path OR (c.path IS NULL AND c.label = $path)
RETURN c.uid AS uid, coalesce(g.uid, g.id) AS trench LIMIT 1`,

  byUid: `
MATCH (c:Context {uid: $uid})-[:IN_TRENCH|IN_SITE]->(g)
RETURN coalesce(g.uid, g.id) AS trench`,
};

export const EXAMPLES = [
  ["Issues by kind",
   `MATCH (i:Issue)
RETURN i.kind AS kind, i.severity AS severity, count(*) AS n
ORDER BY n DESC`],
  ["Period sequence: which period lies directly over which",
   `MATCH (a:Context)-[:ABOVE]->(b:Context),
      (a)-[:DATED_TO]->(pa:Period), (b)-[:DATED_TO]->(pb:Period)
WHERE pa <> pb
RETURN pa.name AS upper, pb.name AS lower, count(*) AS n
ORDER BY n DESC LIMIT 15`],
  ["Everything Locus 20 in D6 seals",
   `MATCH (c:Context {path: 'Area D/Trench 6/Locus 20'})-[:ABOVE*]->(e:Context)
RETURN DISTINCT e.label AS unit, e.period_label AS period, e.depth AS row
ORDER BY row`],
  ["Why is Locus 7042 later than 7044? (the chain of records)",
   `MATCH p = shortestPath((a:Context {path: 'Area F/Trench 7/Locus 7042'})
            -[:ABOVE*]->(b:Context {path: 'Area F/Trench 7/Locus 7044'}))
RETURN [n IN nodes(p) | n.label] AS chain,
       [r IN relationships(p) | r.verbs] AS verbs`],
  ["Finds from units whose date the stratigraphy contradicts",
   `MATCH (i:Issue {kind: 'period_inversion'})-[:INVOLVES]->(c:Context)<-[:FROM]-(f:Find)
OPTIONAL MATCH (f)-[:IDENTIFIED_AS]->(t:Taxon)
RETURN c.path AS unit, c.period_label AS dated, f.label AS find, t.name AS taxon
LIMIT 25`],
  ["Most common taxa, by period",
   `MATCH (p:Period)<-[:DATED_TO]-(:Context)<-[:FROM]-(:Find)-[:IDENTIFIED_AS]->(t:Taxon)
RETURN p.name AS period, t.name AS taxon, count(*) AS n
ORDER BY n DESC LIMIT 20`],
  ["Contested claims and the records that make them",
   `MATCH (a:Context)-[r:CONTESTED]->(b:Context)
RETURN a.path AS upper, b.path AS lower, r.verbs AS verbs, size(r.stated_by) AS records
LIMIT 25`],
];
