# Analysis

What the numbers show, and where I can, why.

Figures are ranges across **two complete runs of identical configuration**,
`20260825T064625Z` and `20260825T072533Z`, both at the corrected 0.5 vCPU / 512 MB
envelope. Both passed verification: all six platforms returned identical values on
**404 checked query results** each time. Tables in [RESULTS.md](RESULTS.md) are
generated from the first; this document is the interpretation and is hand-written.

A range rather than a single number wherever I have one, because a single figure
implies a precision this setup does not have. Where a metric repeated tightly I give
one number.

## The short version

At 0.5 vCPU / 512 MB there is no winner, and the ranking inverts depending on which
question you ask.

- **Memgraph** ingests an order of magnitude faster than anything else and answers
  1-hop and 2-hop fastest.
- **ArangoDB**, the only non-native graph store here, has the **fastest 3-hop**. That
  is the opposite of what the index-free-adjacency argument predicts.
- **FalkorDB** wins throughput outright and loses 3-hop by 5-8x.
- **Neo4j** is an order of magnitude behind on throughput and consumes 99.3% of the
  memory cap plus 542 MB of disk, but its traversal latency improved dramatically once
  it had 512 MB instead of 256 MB.
- **CognoDB** loaded the full graph inside its free tier on every attempt, which
  Memgraph could not do at 256 MB. Its latency cannot be resolved from here.
- **Kùzu**, in-process, answers `RETURN 1` in 0.04-0.20 ms and stores the whole graph
  durably in 9.3 MB.

## 1. Which of these numbers can be trusted

[VARIANCE.md](VARIANCE.md) diffs the two runs. A metric is flagged unstable only when
the relative spread exceeds 25% **and** the absolute difference clears a per-metric
floor, because a 338% spread on 0.04 ms against 0.20 ms is reporting the resolution of
a Python-timed client call, not engine instability.

**Stable across both runs**, on every platform: `RETURN 1`, point lookup, 1-hop,
filtered lookup, group-by, and **all three concurrency levels**.

**Unstable**, and therefore ranges rather than values:

| Metric | Platforms affected | Worst case |
| --- | ---: | --- |
| 3-hop p95 | 2 of 6 | CognoDB, 3,172 ms against 13,830 ms |
| 3-hop p50 | 2 of 6 | FalkorDB, 34.7 ms against 58.1 ms |
| nodes/s | 2 of 6 | Memgraph, 12,119 against 35,714 |
| load total | 2 of 6 | Neo4j, 68.1 s against 83.7 s |

This is a much better position than the earlier 256 MB pair of runs, where saturated
throughput swung by 218% and ingest by 364%. Those runs are in
`results/exploratory/` and are not cited here, both because the envelope was wrong and
because the host was under more pressure.

The honest reading: **ingest rates and 3-hop tails are ranges. Everything else
repeated.** Any difference between two engines smaller than the spread on that page is
not a finding.

## 2. Ingest: a 20x spread, and the batch size explains its shape

| Engine | Total load | rels/s | Index build |
| --- | --- | --- | --- |
| Memgraph | 3.3-4.7 s | 65,822-73,491 | 0.2 s |
| ArangoDB | 7.1-9.1 s | 23,079-30,093 | 0.1 s |
| Kùzu | 24.3-24.8 s | 8,114-8,291 | 0.0 s |
| FalkorDB | 41.6-41.8 s | 4,770-4,794 | 0.2 s |
| Neo4j | 68.1-83.7 s | 4,016-4,232 | **12.6 s** |
| CognoDB | 68.9-69.5 s | 3,258-3,290 | 2.2 s |

**CognoDB's ingest figure is mostly my broadband and should not be read as an engine
result.** The load is 218 round trips (19 node batches + 199 edge batches), and at a
253 ms floor that is ~55 s of the ~69 s total spent waiting on the network. Its
engine-side ingest is therefore roughly 14 s for 216,821 rows, or about 15,000
rows/second, which would put it second in this table rather than last. That correction
is an estimate derived from the baseline, not a measurement, and it is offered as one.

For the local engines the interesting result is not the ranking but that **the batch
size best for one engine is worst for another**. From [CALIBRATION.md](CALIBRATION.md),
total load seconds:

| Engine | 1000 | 2500 | 5000 | 10000 |
| --- | ---: | ---: | ---: | ---: |
| Memgraph | **3 s** | 4-6 s | 13-15 s | 81 s (partial) / 13 s |
| Neo4j | 145 s | **93 s** | 133 s | 118 s |
| ArangoDB | 8 s | 8 s | 9 s | 9 s |
| FalkorDB | **34 s** | 69 s | 160 s | 60 s |

Four shapes, three with mechanical explanations.

**FalkorDB degrades superlinearly** (34 → 69 → 160 s). It stores the graph as sparse
adjacency matrices and each batch commit forces matrix synchronisation; bigger batches
mean more entries folded in per operation and that fold is not linear. The same thing
explains the split inside its own load: **142,593 nodes/s against 4,794 rels/s**, a 30x
gap, because nodes are rows and edges are matrix entries.

**Memgraph degrades because it is fighting a memory ceiling**, not per-batch overhead.
A 10,000-row transaction holds more uncommitted state, and at 256 MB that was the
difference between finishing and not. At 512 MB it has room, which is why its ingest is
now the fastest here by a factor of three.

**ArangoDB is flat** at 8-9 s across the whole sweep, and it is the only engine that
never looks up an endpoint: documents are addressed by primary key, so `_from`/`_to`
are built client-side with no index probe in the write path. A real advantage of the
document model, disclosed rather than normalised away.

**Neo4j is non-monotonic** (145 → 93 → 133 → 118 s), the signature of something other
than batch size dominating. Its 12.6 s index build against 0.1-0.2 s for ArangoDB and
Memgraph is the clue.

## 3. Traversal: native adjacency does not win here

| Engine | 1-hop | 2-hop | **3-hop** | 3-hop p95 |
| --- | --- | --- | --- | --- |
| ArangoDB (non-native) | 1.24-1.37 | 1.65-1.75 | **6.76-7.80** | 57.3 |
| Neo4j (native, disk) | 2.68-2.94 | 3.69-5.72 | 7.51-9.22 | 91.2 |
| Memgraph (native, memory) | **0.47-0.52** | **0.67-1.12** | 11.84-14.85 | 143-199 |
| Kùzu (embedded, columnar) | 1.57-1.60 | 1.79-1.86 | 13.18-16.31 | 243 |
| FalkorDB (sparse matrix) | 0.49-0.54 | 0.88-0.93 | **34.70-58.06** | 462 |

ArangoDB fastest at 3 hops and FalkorDB last by 5-8x held across both runs and across
both memory envelopes, so the ordering is a real result even where the magnitudes are
ranges.

I do not read this as refuting index-free adjacency. I read it as saying the argument is
about a regime this benchmark is not in. Index-free adjacency pays when the alternative
is an index lookup that misses cache and hits disk. At 198,050 relationships every
engine's working set is tens of megabytes and effectively resident, so ArangoDB's
"expensive" probe is a cache hit costing almost nothing. What is left to differentiate
the engines is how much redundant work the traversal does.

And that is where the ordering comes from. **ArangoDB visits each vertex once**, because
it runs BFS with global vertex uniqueness. The Cypher engines enumerate *paths* under
relationship uniqueness and deduplicate at the end with `count(DISTINCT b)`. At 1 hop
those are the same, and Memgraph is nearly 3x faster than ArangoDB there. At 3 hops, on
a graph with mean degree 21, path enumeration does dramatically more work than vertex
visiting, and the ordering flips.

This deserves care, because it is exactly where a benchmark can cheat by accident. The
two formulations are **not different queries**: they return identical answers, verified
across all six platforms on 404 checked values in each run. But they are different
*plans*, and the difference is imposed by what each query language can express, not by
me picking a favourable one. A better study would also run each engine's native BFS
construct, for example Memgraph's `*BFS`. I deliberately did not, because hand-tuning
one engine's query is what makes vendor benchmarks worthless. It is a known gap, listed
in section 9.

**FalkorDB's 3-hop is the outlier most needing explanation.** It is among the fastest at
1 and 2 hops and 5-8x slower than ArangoDB at 3. Under a linear-algebra model a k-hop
expansion is k sparse matrix-vector products, and each product's cost depends on
frontier density: 1-hop touches ~21 nodes, 2-hop ~800, 3-hop ~8,000. The frontier stops
being sparse, and a sparse kernel on a dense-ish vector is the worst case for that
representation. Its p95 of 462 ms against a p50 of 34.7 ms says the same thing.

## 4. Neo4j was memory-starved, and 512 MB proves it

This is the clearest single benefit of catching the tier error. The brief said 256 MB;
the console says 512 MB. Comparing Neo4j across the two envelopes:

| | 256 MB (96 m heap) | 512 MB (192 m heap) |
| --- | --- | --- |
| 3-hop p50 | 13.66 ms | **7.51-9.22 ms** |
| `RETURN 1` p95 / p50 ratio | 24x | ~5x |
| RSS at idle, empty database | 99.9% | 87% |
| qps at 1 client | 51.7 | 158-176 |

Its throughput roughly tripled and its 3-hop nearly halved. At 256 MB, with 89 MB of
heap in use against a 96 MB maximum, its `RETURN 1` p95 was 24x its p50 — and a query
that touches no data cannot produce a graph performance result, so that was garbage
collection. **The engine was not slow; it was periodically not running.**

It still consumes 508.5 MiB of its 512 MiB cap and 542.5 MB of disk. A JVM needs heap,
metaspace, code cache and page cache to all fit, and at this tier that overhead is
decisive in a way it would not be at 8 GB. This is a property of the runtime rather
than of the graph engine, and it is the strongest evidence in the study for the premise
behind CognoDB's positioning, arrived at without taking their word for it.

## 5. Concurrency: half a core means throughput is roughly fixed

| Engine | 1 client | 10 clients | 40 clients | Read p95 @40 |
| --- | --- | --- | --- | --- |
| FalkorDB | 940-1038 | 860-881 | **839-950** | 83.5 ms |
| ArangoDB | 612-627 | 654-706 | 460 | 188.7 ms |
| Memgraph | 558-571 | 357-360 | 433-489 | 114.8 ms |
| Kùzu | 500-508 | 496-510 | 510-523 | 221.7 ms |
| Neo4j | 158-176 | 81-86 | 85-96 | 899.7 ms |
| CognoDB | 3-4 | 38-39 | 89-97 | 700.1 ms |

**Nobody scales, and nobody should.** There is half a core to share, so throughput is
capped by CPU and extra clients convert throughput into queue depth. Latency at 40
clients is 30-100x the single-client figure while qps is flat or down. That is
saturation behaving correctly.

Three things differentiate them, and all three repeated across both runs.

**FalkorDB holds the highest throughput at every level and the lowest tail under 40-way
load.** With `THREAD_COUNT 1` it is essentially a single-threaded event loop, so there
is little contention and queries queue predictably rather than fighting over half a
core. Single-threaded is an advantage when there is less than one core to have.

**Neo4j is an order of magnitude down** with a p95 of 900 ms at 40 clients.

**CognoDB is the only engine whose throughput rises steeply with concurrency**, from ~4
to ~93 qps, a 24x gain. It is the one platform here that is *not* CPU-bound: at a 253 ms
round trip a single client can issue at most about four queries a second no matter how
fast the engine is, so adding clients simply fills the pipe. This is a property of
measuring across an ocean, not a property of CognoDB, and it is the clearest
demonstration of why the two tracks are reported separately.

## 6. What the baseline bought, and where it ran out

| Engine | `RETURN 1` p50 | transport |
| --- | --- | --- |
| Kùzu | 0.04-0.20 ms | none, in-process |
| FalkorDB | 0.39-0.40 ms | RESP |
| Memgraph | 0.45-0.59 ms | Bolt |
| ArangoDB | 1.19-1.61 ms | HTTP + VelocyPack |
| Neo4j | 3.92-3.94 ms | Bolt |
| CognoDB | 253.50-303.97 ms | Bolt over ~12,000 km |

Kùzu's 0.04 ms is the floor: no socket, no serialisation, a function call. So roughly
0.4-0.6 ms of every Bolt query on the local track is protocol plus loopback, and
ArangoDB's HTTP transport costs 1.2-1.6 ms before it does any work. Neo4j's 3.9 ms is
*not* transport, since it speaks the same Bolt as Memgraph's 0.45 ms.

Practically: **some apparent engine wins are transport wins.** FalkorDB's 1-hop
advantage over ArangoDB is 0.49 ms against 1.24 ms, but 0.39 ms and 1.19 ms of those are
baseline, so the engine-only difference is 0.10 ms against 0.05 ms — on which basis
ArangoDB is the faster of the two. The raw numbers say the opposite.

### Where it ran out: CognoDB

Subtracting the baseline from CognoDB's own numbers, in both runs:

| Workload | run 1 delta | run 2 delta |
| --- | --- | --- |
| point lookup | +0.5 ms | **−7.4 ms** |
| 1-hop | +52.9 ms | +0.3 ms |
| 2-hop | +40.5 ms | **−19.3 ms** |
| group-by | +8.9 ms | **−12.0 ms** |
| **3-hop** | **+471.6 ms** | **+558.5 ms** |

The negative values are the point. A query cannot take less time than a no-op, so those
deltas are measuring noise, and they put the noise floor at roughly **±20 ms**. Only
3-hop clears it, consistently, at roughly half a second of engine work.

So: **from India to us-east4, this method cannot resolve CognoDB's per-query engine
latency for anything except 3-hop.** Its published 0.27 ms 2-hop is around 75x below the
noise floor here. That is a limitation of measuring across an ocean, **not a
contradiction of their figure**, and nothing in this study refutes it. Testing it would
need an instance in a nearby region, or a client in us-east4.

## 7. Footprint: durability costs far more than the graph does

| Engine | RSS | % of 512 MB | Store on disk | Durable here? |
| --- | --- | --- | --- | :--: |
| Neo4j | 508.5 MiB | **99.3%** | **542.5 MB** | yes |
| ArangoDB | 440.5 MiB | 86.0% | 75.4 MB | yes |
| Memgraph | 146.5 MiB | 28.6% | 0.2 MB | no |
| FalkorDB | 123.3 MiB | 24.1% | 0 MB | no |
| Kùzu | in-process | n/a | 9.3 MB | yes |
| CognoDB | not observable | - | not observable | assumed yes |

The graph is small: **Kùzu stores all of it, durably, in 9.3 MB.** So Neo4j's 542 MB on
disk is not the graph, it is transaction logs, and it is **more than half the declared
1 GB disk allowance consumed by logging one 198k-relationship load.** On a tier with a
1 GB disk that is arguably a harder constraint than any latency figure here, and a
latency-only benchmark would miss it entirely.

The two engines under 30% of the memory cap are exactly the two that are not durable in
this configuration. That is the largest asymmetry in the study, and both choices
(`--storage-wal-enabled=false`, `--save ''`) are mine. It is not that Memgraph and
FalkorDB are lean; they have been excused from a job the other two are doing.

CognoDB exposes no introspection over Bolt at all: `CALL dbms.*`, `CALL db.info()` and
`SHOW VERSION` are syntax errors. Its console reports node, relationship and storage
figures, but not through the wire, so "not observable" is the honest entry rather than a
number copied off a web page.

## 8. What CognoDB did, and did not, demonstrate

**It loaded the full graph on every attempt** — 18,771 nodes and 198,050 relationships,
zero failed batches, three runs out of three, inside a tier whose memory Memgraph could
not fit the same graph into at 256 MB. Given its stated design point of ~80 bytes per
edge and surviving small instances, this is the claim most directly supported by the
data collected here.

**Its Neo4j compatibility is real at the DDL level.** Neo4j 5 constraint and index
syntax was accepted on the first attempt with no fallback needed, and the server
confirmed all four indexes through `SHOW INDEXES`, composite included. There is no
procedure system, though, which is a genuine compatibility gap for anything relying on
`CALL db.*`.

**3-hop is where it struggles, and the failure is in the connection rather than the
query.** Characterised deliberately rather than guessed: 30 sequential 3-hop queries
gave 24 successes between 326 ms and 17,724 ms, median 1,434 ms. The failures were not
degree-related. One long query (8,975 ms) killed the connection, the next five failed
within ~500 ms each regardless of start node, then it recovered and completed a 17.7 s
query. In the benchmark runs it produced 29-30 valid samples out of 100 with 2-4
reconnects before exhausting its retry budget.

Whether that is CognoDB, its load balancer, or something between here and us-east4 I
cannot tell from the client side, and I am not going to guess. What is defensible: **at
this distance, 3-hop traversals on a 198k-relationship graph did not complete reliably
enough to produce a full 100-iteration sample.**

**Its latency claims were not tested.** See section 6.

## 9. What I would do differently

**Provision in a nearby region.** Everything limiting the CognoDB result traces back to a
253 ms floor. A Mumbai or Singapore instance at 30-50 ms would drop the noise floor by
roughly 5x and make its sub-millisecond operations resolvable. This is the single
highest-value change available.

**More than two runs.** Two give a range. Three to five would give a distribution and
let the report draw error bars instead of a caveat. The harness already supports it; it
is wall-clock time rather than work.

**Report each engine at its own optimal batch size as well as the shared one.** One
value for everyone is right for fairness, but 1000 is Neo4j's worst and costs it 56%
against its own optimum. The sweep data to report both is already collected.

**Close the traversal formulation gap.** The Cypher engines enumerate paths where
ArangoDB visits vertices, and that difference is doing real work in the 3-hop column. A
second, clearly-labelled variant using each engine's native BFS construct would separate
"this engine is slower" from "this query language made it do more work".

**Include hub nodes.** Start nodes are capped at degree 50, which was necessary to stop
3-hop expansions from OOMing the instances. But degree 504 exists in this graph, and
*how* each engine fails on a hub is probably more useful to someone choosing a database
than how it performs on a median node.

**Run on an idle machine.** Less critical than it was at 256 MB, where throughput swung
218% between identical runs, but the ingest and 3-hop ranges here still reflect a laptop
that was also running Docker Desktop and an editor.

**Use a dataset large enough to exceed RAM.** CognoDB's design point is a disk-backed
store with a hot cache traversing a graph larger than memory. At 198,050 relationships
nothing here ever leaves memory, so this benchmark never asks the question that design
answers. A larger dataset would break Memgraph outright, which is exactly why it would
be worth running.
