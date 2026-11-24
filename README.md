# Strata

**An archaeological excavation as one Relate graph file. Every find is connected to the layer it came from, the layers above and below it, its period and its site, and the checks show where the excavators' own records disagree.**

`data/strata.rl` holds the full excavation record of Kenan Tepe, a mound on the Upper Tigris in south-eastern Turkey dug from 1998 to 2007, published openly on [Open Context](https://doi.org/10.6078/M7H41PBJ):

- 3,280 stratigraphic units
- 14,576 recorded relations between them
- 19,742 finds

**Documentation: [openhistorymap.org/strata/docs](https://www.openhistorymap.org/strata/docs/).** Try it in the browser: [openhistorymap.org/strata](https://www.openhistorymap.org/strata/).

Strata turns that record into a Harris matrix you can query, and does the same for **any excavation's matrix** kept in ArchEd, Stratify, pyArchInit, Field Desktop, Harris Matrix Composer, a Graphviz/yEd drawing or a spreadsheet (see [Your own matrices](#your-own-matrices)). An archaeologist or an AI agent can ask it questions in Cypher, from the command line, or over MCP. Edits are proposed in a transaction and refused, with the evidence, when they contradict the record.

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

- **Mode: single file** (`data/strata.rl`, about 44 MB). The [browser viewer](https://www.openhistorymap.org/strata/) also runs the same graph in Relate's in-memory WASM build.
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

### In the browser

**[openhistorymap.org/strata](https://www.openhistorymap.org/strata/)** runs the same graph in your browser tab: Relate's WebAssembly build, in a Web Worker, holding all 23,432 nodes and 40,796 relationships.

- Pick a trench to see its Harris matrix.
- Click a unit for its record, open issues, date bracket, relations and finds. The matrix outlines everything it seals and everything sealing it.
- Every panel has a **Cypher** link that opens the query behind it in the console, where you can edit and rerun it.
- Try `#Area F/Trench 1/Locus 1032` (the "ancient trash" inversion) or `#Area A/Trench 9/Locus 2`, where the excavator's note explains the contradiction.
- Light, dark, or follow your system (the ◐ button).

**Other datasets.** The chip next to the title always says what is loaded and where it came from. Click it to open another dataset:

- **On this site:** every site in `data/strata.rl`, listed in `web/data/datasets.json`. Link one with `?dataset=<site>`.
- **From a URL:** any Strata export served with CORS (GitHub raw, Zenodo, your own server). The page link becomes `?data=<url>`, so you can share it.
- **From your computer:** drop a `.strata.json` file on the page, or pick one. It is read in the tab and nothing is uploaded. The chip turns orange and says **Local file** with the file's name, size and date for as long as it is loaded.

Make that file from any Harris matrix Strata can read (next section):

```bash
uv run strata convert my-dig.lst            # -> my-dig.strata.json, checked
```

Units recorded without a trench (most Harris-matrix files) are drawn as one matrix, "Units without a trench".

To run it locally:

```bash
uv run strata export-web            # data/strata.rl -> web/data/<site>/{core,finds}.json (strata build also does this)
python3 -m http.server -d web 8000  # then open http://localhost:8000
```

The WASM build cannot open a `.rl` file; it runs in memory. So `strata export-web` writes every node and relationship, with all its properties, as JSON. The page loads that into Relate in two parts. The matrix comes first (about 5 s), and the finds stream in behind it while you work. [`tests/web_smoke.mjs`](tests/web_smoke.mjs) loads a dataset into the same WASM build under Node and runs every query the page uses:

```bash
docker run --rm -v "$PWD":/app -w /app node:22-slim node tests/web_smoke.mjs                       # the first built-in dataset
docker run --rm -v "$PWD":/app -w /app node:22-slim node tests/web_smoke.mjs my-dig.strata.json    # a bundle
```

### Ask a model in the browser

**Ask a model** in the viewer's header lets you ask about the excavation in your own words. Sign in with [OpenRouter](https://openrouter.ai/) (OAuth PKCE: the key is your own, billed to your credits, and kept in your browser), pick any model that supports tool use, and it answers by calling the page's own tools against the graph in your tab, showing each Cypher query it runs. Tick **Also use harris-mcp** to add the analysis tools of [harris-mcp](https://github.com/openhistorymap/harris-mcp) (phase sequence, anomalies, topological layers…): the page then sends the trench's matrix to `harris.openhistorymap.org`. Details, including what leaves your browser: [docs](https://www.openhistorymap.org/strata/docs/#ask).

### Give it to an agent

Strata's MCP server adds domain tools on top of Cypher, such as `describe`, `relation`, `date_bracket`, `provenance`, `issues` and `explain_issue`. It also has checked edits:

- `propose_relation`
- `resolve_order`
- `assign_phase`
- `add_note`
- `history` and `revert`

`store_info` says which `.rl` file the server is reading and writing.

**Claude Desktop.** Download `strata.mcpb` from the [latest release](https://github.com/openhistorymap/strata/releases/latest) and open it, or drag it onto *Settings → Extensions*. Claude Desktop installs the Python dependencies itself. The extension carries the Kenan Tepe store as a read-only seed. On first run that seed is copied to `Documents/Strata/strata.rl`, which the server reads and writes, so edits survive updates and the file is yours to see and back up. The extension's *Strata store* setting points it at any other `.rl` file. To build the bundle yourself:

```bash
uv run python desktop/build.py                                   # -> dist/strata-desktop/
npx -y @anthropic-ai/mcpb pack dist/strata-desktop dist/strata.mcpb
```

From a clone instead, add this to `claude_desktop_config.json` (the path to the `.rl` file is optional):

```json
{
  "mcpServers": {
    "strata": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/strata", "strata", "mcp"],
      "env": { "STRATA_DB": "/path/to/strata/data/strata.rl" }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add strata -- uv run --directory "$PWD" strata mcp
```

**Agents in the browser (WebMCP).** The [viewer](https://www.openhistorymap.org/strata/) registers tools with [WebMCP](https://webmachinelearning.github.io/webmcp/) (`document.modelContext`, or `navigator.modelContext` in Chrome 146+ and the MCP-B polyfill). A browser agent can use them to:

- ask what is loaded (`strata_dataset`);
- list trenches and issues;
- describe a unit;
- see how two units relate, with the chain of records;
- run read-only Cypher against the graph in the tab.

It can also drive the page: `strata_show_unit` opens a unit in the matrix, `strata_show_query` runs a query in the console, and `strata_open_dataset` loads another dataset. A **WebMCP** badge appears in the header when the browser supports it, and a toast shows each call an agent makes. These tools only read: the graph in the page is never edited by agents.

Then ask, for example:

- *"Which finds from Kenan Tepe come from units whose date is contradicted by the stratigraphy?"*
- *"Locus 7042 and 7044 disagree. Show me both records and what else orders them."*
- *"Record that Locus 53 lies above Locus 20 in Trench D6."* This one is refused, and the answer is the record chain that makes it impossible.

### Rebuild from source

```bash
uv run strata build                 # data/sources/* -> data/strata.rl, then every check (~20 s)
uv run strata fetch kenan-tepe      # re-download from Open Context and re-normalise (~5 min)
uv run strata import-matrix my.hmcx --site my-dig --name "My dig"   # any format in `strata formats`
uv run pytest
```

## Your own matrices

`strata convert` reads a Harris matrix from the program it was kept in, loads it into a fresh Relate store, runs every check, and writes a `.strata.json` bundle the viewer opens from your disk. `import-matrix` adds it to a store as a site instead, and in Claude Desktop or Claude Code the `import_matrix` tool does the same ("Import ~/Desktop/scavo.sqlite").

```bash
uv run strata convert scavo.sqlite                       # pyArchInit database -> scavo.strata.json
uv run strata convert matrix.lst --store matrix.rl       # also keep the Relate file
uv run strata formats
```

| Format | Read from |
|---|---|
| `lst` | ArchEd, BASP Harris, Stratify `.lst` lists (basic and extended form) |
| `pyarchinit` | pyArchInit's SQLite database, or a CSV of its `us_table` (`rapporti` in Italian, English or German) |
| `field` | Field Desktop (iDAI.field) CSV export, a folder of them, or JSON Lines |
| `dot`, `graphml` | Graphviz, yEd, Gephi, igraph drawings: arrows from the later unit to the earlier (`--reverse` otherwise), edge labels name the relation |
| `edges` | any table with one statement per row: `from, relation, to`, in any of the supported languages |
| `hmcx`, `hmc`, `datapackage`, `csv`, `xlsx`, `hmdp` | Harris Matrix Composer, T. S. Dye's hm packages, paired CSVs, Excel, HMDP, via [harris-mcp](https://github.com/openhistorymap/harris-mcp) |

The format is recognised from the content. Relation words are mapped from English, Italian, German, French and Spanish ("Copre", "Tagliato da", "Wird geschnitten", `isCutBy`…). A word Strata doesn't know is never guessed: it is left out and counted in `skipped_relation_words`. Tested on the stratigraphr LST samples, pyArchInit's sample database, Ulm-Eggingen and Harris's figure 12, and on one invented dig written in every format (`tests/fixtures/matrices/`), which every reader must turn into the same graph. Details: [docs](https://www.openhistorymap.org/strata/docs/#import).

## Where the data lives

- `data/strata.rl` is the persistent Relate store: a single file you can inspect with `relate info --database data/strata.rl`.
- `data/sources/kenan-tepe/` holds the normalised, committed source the store is built from:
  - `contexts.csv`
  - `statements.csv`, one row per relation *as recorded*, with the record that states it
  - `finds.csv`
  - `site.json`
- `data/raw/` holds the cached Open Context JSON. It isn't committed; `strata fetch` recreates it.
- `web/data/<site>/core.json` and `finds.json` are the same graph exported for the browser viewer, and `web/data/datasets.json` lists them. `strata build` regenerates them, and the viewer loads them into Relate's in-memory WASM build.

## Data and licences

- **Code:** Apache-2.0.
- **Kenan Tepe data:** Bradley Parker and Peter Cobb (eds.), *Kenan Tepe*, Open Context, 2012, [doi:10.6078/M7H41PBJ](https://doi.org/10.6078/M7H41PBJ), CC BY 4.0. See [NOTICE](NOTICE).
- **Harris-matrix readers:** from [harris-mcp](https://github.com/openhistorymap/harris-mcp), part of [Open History Map](https://www.openhistorymap.org/).
