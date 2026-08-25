# Walkthrough

A complete guided tour of this project: what it is, why it is built this way, what
every file does, the order things actually happened in, and how the final numbers
were produced.

This is the document to read if you want to understand or defend the whole thing.
It does not repeat the numbers, because those change with every run and would go
stale here. Current figures live in [RESULTS.md](RESULTS.md), the interpretation in
[ANALYSIS.md](ANALYSIS.md).

**Reading order if you are short on time:** sections 1, 2, 6 and 9.

---

## 1. What the assignment asked for

Benchmark **CognoDB Cloud** against at least four other managed graph database
platforms on the same dataset and the same workloads. Publish a reproducible,
honest comparison.

The brief is explicit that it is not grading which database wins. It grades
methodology and fairness (25%), completeness of metrics (20%), reproducibility and
code quality (20%), README and analysis (15%), and communication (20%). It is also
explicit about one specific failure: *"Comparing databases on unequal resources
(e.g. a free tier against a paid tier) is a methodology error."*

Required metrics: ingest throughput, 1/2/3-hop traversal latency, point and
filtered lookups, an aggregation, concurrent mixed read/write throughput, and
resource footprint. At least 100 iterations per read workload, reported as
percentiles rather than averages.

## 2. What we built, in one paragraph

A Python harness (`graphbench`) that loads one canonically-prepared public graph
into six engines through one shared workload definition, times every operation
client-side, **checks that every engine returned the same answer**, and writes the
numbers to JSON from which all tables and charts are generated. The engines run in
two separate tracks that are never combined into one ranking: four in Docker capped
identically to CognoDB's free tier, and CognoDB itself as a managed service. A
fifth, Kùzu, runs embedded as a reference floor. Every judgement call is written
down, and the things the suite cannot measure are stated as plainly as the things
it can.

## 3. The core problem, and the shape of the answer

Almost every published graph benchmark fails in one of two ways.

**Unequal hardware.** Vendor A benchmarks its 8-core instance against vendor B's
free tier. The brief warns about exactly this.

**The "same" query is not the same query.** Cypher and AQL express traversal
differently, and it is trivially easy — by accident or design — to give one engine
a cheaper plan and call the result a performance win.

So the harness is built around three commitments, in order of importance:

1. **One resource envelope**, applied from a single shared YAML anchor so the caps
   cannot drift apart between engines.
2. **One workload definition.** Four of the six engines speak Cypher and run
   byte-identical query text. Only DDL is per-engine, because index syntax is where
   the dialects genuinely diverge.
3. **Verification, not assumption.** Every read operation returns a *value*, not
   just a duration. The runner compares those values across engines and **exits
   non-zero if they disagree**, because a query that is accidentally cheaper on one
   engine is not a fast engine, it is a broken comparison.

Commitment 3 is the one that repeatedly saved the project. See section 6.

## 4. Why these six engines

CognoDB is not a general-purpose enterprise graph database. It is a context graph
for AI agents: Bolt + Cypher compatible, disk-backed with heavy caching, ~80 bytes
per edge, built to run as small isolated instances per session. So comparators were
chosen along the architectural axes that could plausibly explain a difference
against *that* design, not by popularity.

| Engine | Axis | Why it earns a slot |
| --- | --- | --- |
| **CognoDB Cloud** c0 | disk-backed, cached, Cypher/Bolt | the subject |
| **Neo4j** 5.26 Community | native graph, disk, JVM | the reference implementation of Cypher and Bolt that CognoDB claims drop-in driver compatibility with. Without it there is no baseline |
| **Memgraph** 3.12 | native graph, **in-memory**, C++ | CognoDB's own site claims Memgraph crashed under a tight memory cap. That is a testable claim |
| **ArangoDB** 3.12 | **non-native**, RocksDB, multi-model | the only engine that rebuilds adjacency from global indexes instead of storing pointers. The biggest architectural contrast in the set |
| **FalkorDB** 4.20 | **sparse matrix** (GraphBLAS) on Redis | a genuinely different execution model, and a direct rival for the same GraphRAG use case |
| **Kùzu** 0.11 | **embedded**, columnar, no server | architecturally closest to "one small instance per agent", and having no network makes it a measurement floor |

Rejected, with reasons, because omissions need defending too: **Neptune** (no free
tier at all, so matching it means paying then handicapping it), **NebulaGraph**
(three separate processes, will not fit), **JanusGraph** (JVM plus a separate
storage backend), **TigerGraph** (GSQL not Cypher, OLAP-oriented, free tier far
larger), **Dgraph** (not Cypher, free cloud tier gone).

## 5. The code, file by file

```text
config/
  platforms.yaml      what is being compared, tiers, ${ENV} credential refs
  workloads.yaml      iteration counts, hop depths, batch size, mixed-workload mix
src/graphbench/
  paths.py            repo-relative paths, so the CLI works from anywhere
  config.py           loads both YAMLs, resolves ${ENV}, validates invariants
  errors.py           one classifier for "engine out of resources"
  environment.py      client specs, driver versions, git commit, container RSS
  dockerctl.py        destroys and rebuilds local stores between platforms
  metrics.py          Timer, LatencySeries, nearest-rank percentiles
  verify.py           cross-platform agreement, known answers, monotonic hops
  runner.py           orchestration, one platform at a time, writes JSON
  calibrate.py        batch-size sweep and repeat-load variance
  doctor.py           what is configured, what will be skipped
  cli.py              argparse dispatch
  datasets/
    registry.py       which public graphs, with pinned checksums
    fetch.py          download and verify
    sample.py         deterministic snowball sampler
    prepare.py        parse, dedupe, derive properties, emit canonical CSV
    loader.py         streaming read side used by adapters
  adapters/
    base.py           the contract every engine implements
    cypher.py         the shared Cypher workload (4 engines)
    bolt.py           Bolt transport + CognoDB, Neo4j, Memgraph
    falkor.py         Cypher over RESP
    arango.py         AQL
    kuzu.py           embedded, in-process
  workloads/
    reads.py          warm-up then measure, failure handling, reconnect
    mixed.py          concurrency sweep with threads
  report/
    tables.py         markdown tables
    charts.py         matplotlib, log scale, colourblind-safe
    render.py         RESULTS.md + injects the matrix into README
    variance.py       diffs runs of identical config
```

### The pieces that carry the argument

**`adapters/base.py` — the contract.** Every read op returns an `int`. That is not a
style choice; it is what makes results checkable. `load()` is a *template method*
whose sequence (wipe → nodes → indexes → edges) is fixed, so no adapter can reorder
the phases and post a better ingest number.

**`adapters/cypher.py` — the shared workload.** All query strings are built once at
connect time, not per call, because f-string formatting inside a timed loop is a
measurable fraction of a sub-millisecond lookup. Traversal is
`(a {key})-[:REL*1..k]-(b) WHERE b.key <> $key RETURN count(DISTINCT b)`: undirected
pattern, 1..k neighbourhood rather than exact depth.

**`adapters/arango.py` — the hardest fairness problem.** Cypher's variable-length
patterns use *relationship uniqueness*; AQL traversal defaults to *path uniqueness*.
Left alone the two engines answer different questions and the counts diverge.
`OPTIONS {uniqueVertices: 'global', order: 'bfs'}` makes AQL return the same set
`count(DISTINCT b)` reduces to. That is **semantic alignment, not tuning**, and the
proof is that all engines return identical counts.

**`verify.py` — three independent checks.** Cross-platform agreement (same input,
same answer). Known-answer checks (the group-by must sum to the node count; the
relationship count must equal the edge count) — these catch everyone being wrong the
same way, which agreement alone never would. And monotonicity: a k-hop neighbourhood
cannot shrink as k grows, which catches a depth mix-up on a single platform with
nothing to compare against.

**`dockerctl.py` — why it destroys volumes rather than restarting.** Explained in
section 6; it is the scar tissue from the worst bug.

**`metrics.py` — nearest-rank percentiles.** p95 of 100 samples is the 95th smallest
*observation*, something that actually happened, rather than an interpolation between
two things that did. Raw samples are kept in the JSON so anyone can recompute a
different percentile or check that a p95 is not one absurd outlier.

## 6. How it actually went: six bugs, and what each taught

This is the most useful part of the project to understand, because **every one of
these bugs made the numbers look better, not worse.** That is the direction
benchmark bugs run, and it is the entire argument for checking returned values
rather than only timing them.

### Bug 1: a wipe that killed the database

Deleting the graph 10,000 nodes at a time means detaching 100,000+ relationships in
one transaction. Against Neo4j's heap that produced
`OutOfMemoryError: Java heap space`, then a dropped connection, then every
subsequent query failing on a defunct socket.

**Fix:** the delete batch starts at 2,000 and halves on a resource error, resetting
the session first because the OOM takes the socket with it.
**Lesson:** setup code that is not measured can still destroy the measurement.

### Bug 2: a reload into memory that was never freed

Memgraph loaded all 198,050 relationships, then failed at 5,000 on the next run *in
the same process*: `Memory limit exceeded! Current use is 202.86MiB`. `wipe()` had
deleted the data and reported success, but Memgraph does not return freed memory to
its allocator, so the reload began with the budget already spent.

Left alone this produced a **3-hop p50 of 0.64 ms** — a spectacular number, measured
against a graph holding 5,000 of 198,050 edges. With a clean store the same query is
14 ms.
**Lesson:** "the wipe returned success" is not the same as "the engine is empty".

### Bug 3: a restart that restored the problem

So the runner began restarting containers. Not enough. Memgraph writes a snapshot on
exit even with periodic snapshots and the WAL disabled, so the restarted container
*recovered the previous run's graph* and OOMed again, this time at 15,000 nodes with
zero relationships.

**This is the one the verification caught.** Memgraph answered `0` where the other
three answered `42`, and the run exited non-zero instead of publishing a table of
plausible-looking nonsense.
**Fix:** `compose rm -sf` + delete the volumes + bring it back, before every load.
**Lesson:** this is what commitment 3 is for. No amount of careful timing would have
noticed.

### Bug 4: a classifier that had silently drifted

Two copies existed of "is this an out-of-resources error", one in the wipe retry and
one in the read workload's abandon check. The read side did not match Memgraph's
actual message — none of `outofmemory`, `out of memory` or `heap space` appear in
`Memory limit exceeded!` — so a Memgraph OOM mid-workload was treated as transient
and retried for all 100 iterations, filling the percentiles with retry noise.

**Found by writing a test**, not by running the benchmark. Now one shared classifier
in `errors.py`, and every string in `test_errors.py` is one a real engine produced.

### Bug 5: the tier was twice the size we thought

The brief states the CognoDB free tier is "burstable 0.5 vCPU, **256 MB** RAM, 1 GB
disk". A provisioned instance reports **`c0 · 512 MB`**, confirmed twice in its own
console. So every comparator had been capped at *half* the memory the subject
actually gets — a fairness error in the direction that makes the comparators look
worse.

**Fix:** recapped at 512m and retuned each engine to the real envelope. Neo4j went
from a 96 MB heap idling at 99.9% of its limit to a 192 MB heap at 87%.
**Lesson:** verify vendor specs against a provisioned instance, not documentation.

### Bug 6: nearly publishing something unfair to CognoDB

CognoDB's 3-hop came back `ABANDONED` on
`ServiceUnavailable: Failed to read from defunct connection`. Probed immediately
afterwards, 3-hop worked on every start degree tried and returned counts matching
all four other engines exactly. It was a **lost packet on a 240 ms intercontinental
link**, not an engine limitation.

The abandon-on-dropped-socket rule was written for local engines, where that means
the engine died. Over the internet it means a packet got lost, and discarding the
other 99 iterations published a missing row that reads as *"CognoDB cannot run this
query"*.
**Fix:** connection errors get one retry through a fresh connection; memory errors do
not, because a reconnect fixes a packet and does nothing for an exhausted heap.
Reconnects are counted into the results.
**Lesson:** a benchmark's failure handling encodes assumptions about *why* things
fail, and those assumptions differ between loopback and the public internet.

## 7. How the numbers were produced

### The dataset

SNAP **ca-AstroPh**, the arXiv astrophysics co-authorship network.
**18,771 nodes / 198,050 relationships** after dedupe, sha256 pinned and re-verified
on every fetch.

Our counts differ from SNAP's published 18,772 / 198,110 **on purpose**: there are
exactly 60 self-loops, and the one extra node they count (id 64582) appears only in
a self-loop. Verified against the raw file, not assumed. Self-loops go because they
make "1-hop neighbours" ambiguous across engines.

SNAP edge lists have no attributes, but the brief needs a point lookup, a filtered
lookup and a group-by. So `key`, `cohort` and `degree` are **derived** from the node
id rather than randomly generated, which means every platform gets byte-identical
properties by construction, and a stranger reproduces them from the raw file alone.
`cohort` uses crc32, not Python's `hash()`, because `hash()` on a string is salted
per process.

Start nodes come from a **degree band of 5 to 50**, not uniform random. A 3-hop
expansion from a degree-504 hub measures the OOM killer, and under uniform sampling
p95 would mostly record whether a run happened to draw a hub. This is a real
limitation, stated as one: **these are mid-degree traversals and hub behaviour is
deliberately out of scope.**

### The batch size was measured, not chosen

`workloads.yaml` originally shipped 5000 with a comment admitting it was a guess.
`graphbench calibrate` swept 500/1000/2500/5000/10000 against every engine, from a
destroyed-and-rebuilt store each time. The optimum turned out to be **engine-specific
and mechanically explicable**:

- FalkorDB degrades superlinearly, because each batch commit folds new edges into a
  sparse matrix and that fold is not linear in the entry count
- Memgraph degrades because larger transactions hold more uncommitted state against
  its memory ceiling, and it is **non-deterministic at 10000** — one attempt
  completed, another stopped at 170,000 edges
- ArangoDB is flat, because it never looks up an endpoint at all
- Neo4j is non-monotonic, the signature of GC rather than batch size dominating

Settled on **1000**: every engine completes, Memgraph has the widest memory headroom
and tightest variance, FalkorDB is fastest. It costs Neo4j 56% against its own
optimum, which is the price of one value everywhere instead of per-engine tuning, and
that price is stated rather than absorbed. Full table in
[CALIBRATION.md](CALIBRATION.md).

### The measurement protocol

For each platform, one at a time and never in parallel (two engines under load on
one host would contend, and the numbers would describe the host):

1. **Destroy and rebuild** the store (local track only; cloud cannot be reset, which
   is a stated asymmetry)
2. **Load** in three separately-timed phases: nodes, indexes, edges. Indexes go
   *between* the data phases — before the nodes a unique constraint slows every
   insert, after the edges the edge phase has to full-scan for endpoints
3. **20 warm-up iterations, discarded.** The very first call is kept separately as
   `first_call_ms`, and labelled as *not* a true cold start, because the engine's
   caches are hot from having just written the data
4. **100 measured iterations** per read workload, start keys rotating so a
   100-iteration run touches 100 different nodes
5. **Mixed workload** at 1 / 10 / 40 clients, 30s each, 90/10 read/write, reads split
   half point-lookup half 2-hop, interleaving driven by a seeded RNG so a lucky run
   with fewer writes cannot post a better number
6. **Footprint**, from `docker stats` outside the engine plus whatever the engine
   reports about itself. "Not observable" where it is not, never estimated
7. **Verify**, then write JSON

### Why `RETURN 1` is a measured workload

CognoDB only exists as a managed service and its instance is in **us-east4**, while
the capped containers are on loopback. Without a no-op baseline, comparing their
traversal latency is partly a speed test of a domestic broadband connection.

`RETURN 1` exercises driver, protocol and network with the graph engine contributing
nothing, so subtracting it leaves something much closer to engine work. Kùzu, being
in-process, anchors the zero-network end of the same scale at ~0.04 ms.

This turned out to matter enormously. CognoDB's baseline is ~240 ms, which means its
point lookup and 1-hop come out **below the resolution of the method** — consistent
with its published sub-millisecond claims rather than contradicting them — while its
2-hop and group-by are resolvable. Reporting the raw numbers without the baseline
would have been meaningless.

### The exact commands

```bash
make setup                   # venv on python 3.13, exact pinned deps
cp .env.example .env         # credentials, gitignored, env only
make dataset                 # download, verify sha256, emit canonical CSV
make up                      # capped stack, waits for healthy
make doctor                  # what is reachable, what gets skipped
graphbench calibrate         # batch size sweep -> docs/CALIBRATION.md
make bench                   # the run -> results/<run-id>/
make report                  # -> docs/RESULTS.md, charts, README matrix
graphbench compare           # -> docs/VARIANCE.md
```

## 8. Reading the output honestly

Two runs of byte-identical configuration disagree by 60-360% on ingest and
40-client throughput, while agreeing to within 6% on `RETURN 1`. The cause is almost
certainly that **the client machine was not idle** — an 8-core laptop also running
Docker Desktop, an editor, and active development.

So the honest reading is:

- `RETURN 1`, point lookup and 1-hop p50 **are** trustworthy
- Ingest throughput and saturated throughput are **ranges, not values**. They support
  "an order of magnitude slower" and do not support "30% faster"
- Any difference smaller than the spread in [VARIANCE.md](VARIANCE.md) is not a
  finding

`graphbench compare` generates that document precisely so the caveat cannot drift
away from the data it is a caveat about.

## 9. Defending it: the questions worth rehearsing

**"How do you know the queries are equivalent?"** Every read op returns a value, the
runner compares them across all platforms, and the run exits non-zero on
disagreement. On the published run every platform agreed on 404 checked values. Plus
two independent checks that agreement alone cannot make: known-answer (group-by must
sum to the node count) and monotonicity (a k-hop neighbourhood cannot shrink).

**"Isn't ArangoDB's traversal a different query?"** It is a different *plan*, forced
by what AQL can express, and it returns an identical answer. Cypher enumerates paths
under relationship uniqueness; AQL visits vertices under global uniqueness. I did
*not* give the Cypher engines a BFS hint (Memgraph has `*BFS`), because hand-tuning
one engine's query is what makes vendor benchmarks worthless. It is listed as a known
gap in [ANALYSIS.md](ANALYSIS.md) section 7.

**"Why is the local track fair when CognoDB is in the cloud?"** It is not directly
comparable, which is why they are separate tracks that are never merged into one
ranking, and why `RETURN 1` is measured on every platform.

**"Why not just use each free tier as-is?"** Because they are not equal to each other
(Aura Free 1 GB, Memgraph Cloud 2 GB against CognoDB's 512 MB) and none can be dialled
down to match. Presenting that as one league table is the exact error the brief warns
about.

**"Your coverage is 66%, not 80%."** The gap is concentrated in one place on purpose:
the transport half of each adapter. Mocking a Bolt session tests the mock. Those
paths are covered by real runs against the capped containers. What *is* unit tested is
query construction, because that is where a silent regression would still produce
plausible numbers while measuring something else.

**"Did CognoDB win?"** Wrong question, and the README says so in its first paragraph.
At this tier "did it finish at all" mattered more than any latency number: CognoDB
loaded the full graph inside c0 while Memgraph could not at 256 MB. Its
sub-millisecond claims could not be verified from India, and that is a limitation of
my measurement, not a contradiction of theirs.

**"What would you do differently?"** Run on an idle machine — that undermines every
throughput number here. Three to five runs instead of two, so the report shows error
bars instead of a caveat. Include hub nodes, because how each engine *fails* on a
degree-504 node is more useful than how it performs on a median one. And use a
dataset large enough to exceed RAM, which is the regime CognoDB's disk-backed design
exists for and the one this benchmark never enters.
