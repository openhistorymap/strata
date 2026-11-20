# Strata

**An archaeological excavation as one Relate graph file. Every find is connected to the layer it came from, the layers above and below it, its period and its site, and the checks show where the excavators' own records disagree.**

`data/strata.rl` holds the full excavation record of Kenan Tepe, a mound on the Upper Tigris in south-eastern Turkey dug from 1998 to 2007, published openly on [Open Context](https://doi.org/10.6078/M7H41PBJ):

- 3,280 stratigraphic units
- 14,576 recorded relations between them
- 19,742 finds

Strata turns that record into a Harris matrix you can query. An archaeologist or an AI agent can ask it questions in Cypher, from the command line, or over MCP. Edits are proposed in a transaction and refused, with the evidence, when they contradict the record.

## Use case

Excavations record stratigraphy one unit at a time. Each locus sheet says "this is above 17, 49 and 53; it is cut by 29". Put together, those statements form a directed acyclic graph: the [Harris matrix](https://en.wikipedia.org/wiki/Harris_matrix). The dig's chronology is paths through that graph.

- **Dating:** a sherd is no older than anything sealed beneath it.
- **Ordering:** two units are in sequence only if a chain of superposition connects them.

The matrix itself is usually drawn by hand, after the season, from paper or spreadsheets. Contradictions between records are found late or never. Current tools draw matrices but can't be queried.

The relationships that matter here are:

- **superposition** between units: above, cuts, fills, seals
- **identity:** the same unit recorded twice
- **containment:** finds bag → locus, locus → trench → site
- **dating:** unit → period, find → taxon (GBIF)
- **outward links:** site → Wikidata

Strata helps three groups:

- **Field directors and specialists** get the questions they ask every day as one query each:
  - *what does this pit seal?*
  - *why is this floor later than that wall?*
  - *what is the date bracket for this burial?*
  - *where did this bone come from?*
- **Data editors and reviewers** of a published archive get every place where the records disagree, each traced to the records that disagree.
- **Agents** helping with post-excavation analysis get a graph they can reason over and safely edit through MCP. Every edit is checked against the stratigraphy before it commits.

### What the graph found in the published record

`strata check` found 268 open issues in Kenan Tepe's published locus records. Each issue links to the units involved, and each disputed relationship keeps the records (and Open Context URIs) that state it:

| issue | n | what it means |
|---|---:|---|
| self_contradiction | 86 | one locus record lists the same unit as both above and below it |
| same_as_conflict | 66 | two units recorded as one, yet one lies above the other |
| elevation_conflict | 28 | recorded above a unit whose bottom elevation is higher than its own top |
| cut_or_cover | 26 | A "cuts" B by one record, B lies "above" A by another; the classic cut/fill ambiguity |
| conflicting_records | 23 | two records order the same pair in opposite directions |
| cycle | 16 | a longer loop: A above B above C above A |
| enclosed | 15 | above, below *and* within the same unit: an inclusion, not an order (info) |
| period_inversion | 8 | a Late Chalcolithic unit lying above an Early Bronze Age I one |

The period inversions are where connected context pays off. Locus 1032 in Area F, Trench 1 is dated Late Chalcolithic but lies above an EBA I unit. `strata ask describe` brings up the excavator's own note on it: *"…these bricks were never able to connect with anything, and may have been ancient trash."* That is redeposited older material, which the graph flagged without being told to look for it.

## How it uses Relate

- **Mode: single file** (`data/strata.rl`, about 44 MB).
- **Why this mode:**
  - An excavation archive is a document. It gets deposited, cited, copied to a field laptop that has no network, and handed to the next specialist.
  - One `.rl` file is all of that at once. Relate opens it in-process, with no server, so the same file works with the Python package, the `relate` CLI, `relate serve --mcp`, and Strata's own MCP server.
  - Relate's WAL makes every accepted edit durable.
  - Each edit is one transaction, so an agent's proposal either commits whole (with its log entry) or leaves no trace.
- **What the graph models:**

```
(:Site)<-[:IN_SITE]-(:Trench)<-[:IN_TRENCH]-(:Context)            a Context is a stratigraphic unit (locus)
(:Context)-[:ABOVE {verbs, stated_by, sources}]->(:Context)       later -> earlier; which records say so
(:Context)-[:CONTESTED]->(:Context)                               an ABOVE claim set aside: it contradicts another
(:Context)-[:REJECTED]->(:Context)                                a claim an archaeologist ruled out (kept, not deleted)
(:Context)-[:SAME_AS|ABUTS|WITHIN]->(:Context)
(:Find)-[:FROM {bag}]->(:Context)        (:Find)-[:IDENTIFIED_AS]->(:Taxon {uri: GBIF})
(:Context)-[:DATED_TO {basis}]->(:Period)-[:OF_SITE]->(:Site {wikidata})
(:Context)-[:IN_PHASE]->(:Phase)
(:Issue {kind, severity, detail})-[:INVOLVES]->(:Context)         check results, stored in the file
(:Edit {op, args, note, author, ts})-[:TOUCHED]->(:Context)       interpretive history, stored in the file
```

All of the reasoning runs as Cypher inside Relate (see [`strata/checks.py`](strata/checks.py)):

- **Matrix rows:** each unit's row (`Context.depth`) comes from peeling the ABOVE graph one layer per statement (Kahn's algorithm). That's 37 layers for Kenan Tepe.
- **Contradictions:** reciprocal two-way claims and longer loops are found with pattern matching and `shortestPath`, then retyped `:CONTESTED`, so the remaining matrix is a clean DAG.
- **Drawing:** edges implied by a longer path are flagged `redundant`. The transitive reduction is what a Harris matrix draws.
- **Questions:** date brackets, provenance and "everything this unit seals" are variable-length traversals. [`strata/queries.py`](strata/queries.py) has them all.

A full rebuild (import plus every check) takes about 20 seconds. Every question answers in milliseconds.

## Setup and run

Prerequisites: Python 3.11+ and [uv](https://docs.astral.sh/uv/). Optional: the [`relate` CLI](https://relatedb.ai/docs/get-started/install).

```bash
git clone https://github.com/openhistorymap/strata
cd strata
uv sync
```

The store is in the repository, so you can query it right away:

```bash
uv run strata ask overview
uv run strata ask issues --site kenan-tepe --kind period_inversion
uv run strata ask describe "Area F/Trench 1/Locus 1032"
uv run strata ask relation "Area F/Trench 7/Locus 7042" "Area F/Trench 7/Locus 7044"
uv run strata ask below "Area D/Trench 6/Locus 20"
uv run strata ask bracket "Area D/Trench 6/Locus 111"
uv run strata ask provenance "Bone-ID KT00090"
uv run strata query "MATCH (c:Context)-[:DATED_TO]->(p:Period) RETURN p.name, count(c) ORDER BY count(c) DESC"
```

With the Relate CLI on the same file:

```bash
relate --database data/strata.rl "MATCH (i:Issue) RETURN i.kind, count(*) ORDER BY count(*) DESC"
relate serve --database data/strata.rl --mcp stdio      # Relate's own MCP surface: raw Cypher
```

### Give it to an agent

Strata's MCP server adds domain tools on top of Cypher, such as `describe`, `relation`, `date_bracket`, `provenance`, `issues` and `explain_issue`. It also has checked edits:

- `propose_relation`
- `resolve_order`
- `assign_phase`
- `add_note`
- `history` and `revert`

```bash
claude mcp add strata -- uv run --directory "$PWD" strata mcp
```

Then ask, for example:

- *"Which finds from Kenan Tepe come from units whose date is contradicted by the stratigraphy?"*
- *"Locus 7042 and 7044 disagree. Show me both records and what else orders them."*
- *"Record that Locus 53 lies above Locus 20 in Trench D6."* This one is refused, and the answer is the record chain that makes it impossible.

### Rebuild from source

```bash
uv run strata build                 # data/sources/* -> data/strata.rl, then every check (~20 s)
uv run strata fetch kenan-tepe      # re-download from Open Context and re-normalise (~5 min)
uv run strata import-matrix my.hmcx --site my-dig --name "My dig"   # any format harris-mcp reads
uv run pytest
```

## Where the data lives

- `data/strata.rl` is the persistent Relate store: a single file you can inspect with `relate info --database data/strata.rl`.
- `data/sources/kenan-tepe/` holds the normalised, committed source the store is built from:
  - `contexts.csv`
  - `statements.csv`, one row per relation *as recorded*, with the record that states it
  - `finds.csv`
  - `site.json`
- `data/raw/` holds the cached Open Context JSON. It isn't committed; `strata fetch` recreates it.

## Data and licences

- **Code:** Apache-2.0.
- **Kenan Tepe data:** Bradley Parker and Peter Cobb (eds.), *Kenan Tepe*, Open Context, 2012, [doi:10.6078/M7H41PBJ](https://doi.org/10.6078/M7H41PBJ), CC BY 4.0. See [NOTICE](NOTICE).
- **Harris-matrix readers:** from [harris-mcp](https://github.com/openhistorymap/harris-mcp), part of [Open History Map](https://www.openhistorymap.org/).
