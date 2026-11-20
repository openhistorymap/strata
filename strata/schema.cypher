// Strata graph schema, version 1.
//
// (:Site)<-[:IN_SITE]-(:Trench)<-[:IN_TRENCH]-(:Context)
// (:Context)-[:ABOVE {verbs, stated_by, sources}]->(:Context)   upper/later -> lower/earlier
// (:Context)-[:SAME_AS]->(:Context)   (:Context)-[:ABUTS]->(:Context)   (:Context)-[:WITHIN]->(:Context)
// (:Find)-[:FROM {bag}]->(:Context)   (:Find)-[:IDENTIFIED_AS]->(:Taxon)
// (:Context)-[:DATED_TO {basis}]->(:Period)-[:OF_SITE]->(:Site)
// (:Context)-[:IN_PHASE]->(:Phase)-[:OF_SITE]->(:Site)
// (:Edit)-[:TOUCHED]->(:Context)      every interpretive change, kept in the graph
// (:Issue)-[:INVOLVES]->(:Context)    every check result, kept in the graph

CREATE CONSTRAINT site_id IF NOT EXISTS FOR (s:Site) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT trench_uid IF NOT EXISTS FOR (t:Trench) REQUIRE t.uid IS UNIQUE;
CREATE CONSTRAINT context_uid IF NOT EXISTS FOR (c:Context) REQUIRE c.uid IS UNIQUE;
CREATE CONSTRAINT find_uid IF NOT EXISTS FOR (f:Find) REQUIRE f.uid IS UNIQUE;
CREATE CONSTRAINT period_uid IF NOT EXISTS FOR (p:Period) REQUIRE p.uid IS UNIQUE;
CREATE CONSTRAINT phase_uid IF NOT EXISTS FOR (p:Phase) REQUIRE p.uid IS UNIQUE;
CREATE CONSTRAINT taxon_uri IF NOT EXISTS FOR (t:Taxon) REQUIRE t.uri IS UNIQUE;
CREATE CONSTRAINT edit_id IF NOT EXISTS FOR (e:Edit) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (i:Issue) REQUIRE i.id IS UNIQUE;
CREATE INDEX context_site IF NOT EXISTS FOR (c:Context) ON (c.site);
CREATE INDEX context_label IF NOT EXISTS FOR (c:Context) ON (c.label);
CREATE INDEX context_depth IF NOT EXISTS FOR (c:Context) ON (c.depth);
CREATE INDEX find_label IF NOT EXISTS FOR (f:Find) ON (f.label);
CREATE INDEX issue_kind IF NOT EXISTS FOR (i:Issue) ON (i.kind)
