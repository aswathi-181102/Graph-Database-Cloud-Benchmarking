# Analysis

What the numbers show, and where I can, why.

Figures are from run `20260824T093849Z` on the resource-matched track (0.5 vCPU /
256 MB) unless stated. Tables in [RESULTS.md](RESULTS.md) are generated; this
document is the interpretation and is hand-written.

**CognoDB is not in these numbers.** No instance was provisioned for this run, so it
is reported as skipped rather than estimated. Everything below describes the four
capped server engines plus Kùzu as an embedded floor. Adding CognoDB is three
environment variables and a rerun.

## Read this first: most of these metrics do not survive repetition

Two full runs were done with byte-identical configuration. They disagree, and the
disagreement is not uniform. From [VARIANCE.md](VARIANCE.md), counting platforms
whose figure moved by more than 25% between the two runs:

| Metric | Platforms unstable |
| --- | ---: |
| load total (s) | 3 of 5 |
| nodes/s | 3 of 5 |
| rels/s | 3 of 5 |
| group-by p50 | 3 of 5 |
| qps @40 | 3 of 5 |
| 3-hop p50 | 2 of 5 |
| point p50 | 1 of 5 |
| **`RETURN 1` p50** | **0 of 5** |

The worst single case is ArangoDB's node ingest rate: **54,201/s in one run and
11,673/s in the other**, a 364% spread on the same engine, same data, same batch
size, same store rebuilt from scratch both times.

**The most likely cause is my own fault and needs stating plainly: the client machine
was not idle.** Both runs happened on an 8-core laptop that was simultaneously
running Docker Desktop, an editor, and my own development work. The containers are
CPU-capped, but the Python client, the Docker VM and everything else are not, and
ingest is the most CPU-bound phase in the suite. A benchmark like this needs a
quiescent host and did not get one.

So the honest reading of the whole results table is:

- **`RETURN 1`, point lookup and 1-hop p50 are trustworthy.** They repeated within
  about 10%, and the no-op repeated within 6% on every engine.
- **Ingest throughput and 40-client throughput are ranges, not values.** A 60-360%
  spread means those columns can support statements like "Neo4j is an order of
  magnitude slower" and cannot support "engine A beats engine B by 30%".
- **The qualitative orderings below held across both runs.** Where I make a claim, I
  have checked it survived repetition, and where it did not, I say so.

Everything after this point is written with that constraint in mind. It is the most
important caveat in the project and it is the reason the variance report is generated
rather than described.

## 1. Ingest: an order of magnitude, and the batch size explains the shape

| Engine | Total load | nodes/s | rels/s | Index build |
| --- | ---: | ---: | ---: | ---: |
| Memgraph | 4.3 s | 30,923 | 57,767 | 0.2 s |
| ArangoDB | 14.0 s | 11,673 | 16,242 | 0.2 s |
| Kùzu | 39.5 s | 26,361 | 5,124 | 0.1 s |
| FalkorDB | 49.6 s | 43,395 | 4,124 | 1.2 s |
| Neo4j | 162.3 s | 745 | 1,814 | **28.0 s** |

The Memgraph-to-Neo4j gap is roughly 38x here and was 28x in the other run. The
ordering is stable even though the magnitudes are not, so "Memgraph ingests an order
of magnitude faster than Neo4j at this tier" is supportable and "38x" is not.

The genuinely interesting result is not the ranking, it is that **the batch size that
is best for one engine is worst for another**. Total load time by batch size, from
[CALIBRATION.md](CALIBRATION.md):

| Engine | 1000 | 2500 | 5000 | 10000 |
| --- | ---: | ---: | ---: | ---: |
| Memgraph | **3 s** | 4-6 s | 13-15 s | 81 s (partial) / 13 s |
| Neo4j | 145 s | **93 s** | 133 s | 118 s |
| ArangoDB | 8 s | 8 s | 9 s | 9 s |
| FalkorDB | **34 s** | 69 s | 160 s | 60 s |

Four different shapes, and three have a mechanical explanation.

**FalkorDB degrades superlinearly** (34 → 69 → 160 s). It stores the graph as sparse
adjacency matrices, and each batch commit forces matrix synchronisation. Bigger
batches mean more entries folded into the matrix per operation, and that fold is not
linear in the entry count. It also explains the split inside its own load: 43,395
nodes/s against 4,124 rels/s. Nodes are rows; edges are matrix entries.

**Memgraph degrades because it is fighting its memory ceiling**, not per-batch
overhead. A 10,000-row transaction holds more uncommitted state, and at a 200 MiB
limit that is the difference between finishing and not. Its slow runs are the ones
close to the limit, which is why 5,000 rows costs four times what 1,000 rows costs
rather than a quarter.

**ArangoDB is flat** at 8-9 s across the entire sweep, and it is the only engine that
never looks up an endpoint: documents are addressed by primary key, so `_from`/`_to`
are built client-side with no index probe in the write path. With nothing to probe,
batch size barely matters. That is a real advantage of the document model, it is not
normalised away, and it is disclosed in the README.

**Neo4j is non-monotonic** (145 → 93 → 133 → 118 s), which is the signature of
something other than batch size dominating. Its 28-second index build against 0.2 s
for Memgraph and ArangoDB is the clue: with a 96 MB heap and 48 MB page cache it is
doing GC and page eviction throughout, and where that lands relative to batch
boundaries is close to arbitrary.

## 2. Traversal: native adjacency does not win here, and scale is the reason

The orthodox claim is that native graph databases beat non-native ones on multi-hop
traversal, because they follow pointers instead of rebuilding adjacency from indexes.
At 3 hops on this dataset, the ordering is the reverse:

| Engine | 1-hop p50 | 2-hop p50 | 3-hop p50 | 3-hop p95 |
| --- | ---: | ---: | ---: | ---: |
| ArangoDB (non-native) | 1.32 ms | 1.93 ms | **5.87 ms** | 62.9 ms |
| Neo4j (native, disk) | 6.72 ms | 5.66 ms | 13.66 ms | 271.2 ms |
| Memgraph (native, in-memory) | 0.46 ms | 0.90 ms | 13.88 ms | 173.6 ms |
| Kùzu (embedded, columnar) | 1.76 ms | 1.76 ms | 18.51 ms | 242.0 ms |
| FalkorDB (sparse matrix) | 0.41 ms | 1.20 ms | **58.05 ms** | 526.4 ms |

ArangoDB fastest at 3 hops and FalkorDB last by an order of magnitude both held
across the two runs, so the ordering is a real result even though the exact numbers
are not.

I do not read this as refuting index-free adjacency. I read it as saying the argument
is about a regime this benchmark is not in. Index-free adjacency pays when the
alternative is an index lookup that misses cache and hits disk. At 198,050
relationships every engine's working set is tens of megabytes and effectively
resident, so ArangoDB's "expensive" probe is a cache hit costing almost nothing. What
is left to differentiate the engines is how much redundant work the traversal does.

And that is where the ordering comes from. **ArangoDB visits each vertex once**,
because it runs BFS with global vertex uniqueness. The Cypher engines enumerate
*paths* under relationship uniqueness and deduplicate at the end with
`count(DISTINCT b)`. At 1 hop those are identical. At 3 hops, on a graph with mean
degree 21, path enumeration does dramatically more work than vertex visiting.

This deserves care, because it is exactly where a benchmark can cheat by accident.
The two formulations are **not different queries**: they return identical answers,
verified across all five platforms on 404 checked values. But they are different
*plans*, and the difference is imposed by what each query language can express, not
by me picking a favourable one. A better comparison would also run each engine's
native BFS construct, for example Memgraph's `*BFS`. I deliberately did not, because
hand-tuning one engine's query is what makes vendor benchmarks worthless and I would
then owe the same effort to all five. It is a known gap, listed in section 7.

**FalkorDB's 3-hop is the outlier most in need of explaining.** It is fastest at
1 hop and 10x slower than ArangoDB at 3. Under a linear-algebra model a k-hop
expansion is k sparse matrix-vector products, and each product's cost depends on
frontier density. 1-hop touches ~21 nodes, 2-hop ~800, 3-hop ~8,000. The frontier
stops being sparse, and a sparse kernel on a dense-ish vector is the worst case for
that representation. Its p95 of 526 ms against a p50 of 58 ms says the same: runs
that draw a higher-degree start node fall off a cliff rather than degrading smoothly.

## 3. For Neo4j the tail is the whole story

Neo4j's p50s are unremarkable but defensible. Its p95s are not:

| Workload | p50 | p95 | ratio |
| --- | ---: | ---: | ---: |
| `RETURN 1` | 2.99 ms | 73.20 ms | 24x |
| point lookup | 4.08 ms | 70.42 ms | 17x |
| 1-hop | 6.72 ms | 187.57 ms | 28x |
| 3-hop | 13.66 ms | 271.18 ms | 20x |

A p95/p50 ratio of 24x **on a query that touches no data** is not a graph performance
result. `RETURN 1` does no work, so it can only be measuring the driver, the socket,
and whatever the server was doing instead of answering. The JVM reports 89 MB of heap
in use against a 96 MB maximum and the container sits at 99.9% of its 256 MB, so the
answer is almost certainly garbage collection. **The engine is not slow at graph
queries. It is periodically not running.**

This is the clearest argument in the whole study for percentiles over averages.
Neo4j's mean would land somewhere in the middle and describe neither the 3 ms common
case nor the 73 ms stall.

It also supports something CognoDB's positioning implies without my having to take
their word for it. A JVM engine needs heap, metaspace, code cache and page cache to
all fit. Neo4j gets 144 MB of the 256 for heap plus page cache, and the remaining
~110 MB is overhead doing no useful work for any query. The C++ and Redis-based
engines spend nearly all of their allowance on data. At this tier that is decisive,
and it is a property of the runtime rather than of the graph engine.

## 4. Concurrency: with half a core, throughput is roughly fixed

| Engine | 1 client | 10 clients | 40 clients | Read p95 @40 |
| --- | ---: | ---: | ---: | ---: |
| FalkorDB | 996.6 | 801.7 | 692.8 | 101.3 ms |
| ArangoDB | 613.3 | 432.5 | 135.5 | 905.5 ms |
| Memgraph | 581.1 | 358.6 | 483.9 | 183.5 ms |
| Kùzu | 447.1 | 491.9 | 477.4 | 235.8 ms |
| Neo4j | 51.7 | 51.5 | 26.6 | 2402.1 ms |

**Nobody scales, and nobody should.** There is half a core to share, so total
throughput is capped by CPU and extra clients convert throughput into queue depth.
Latency at 40 clients is 30-100x the single-client figure while qps is flat or down.
That is saturation behaving correctly, not a defect.

Two claims here survive repetition and one does not.

**FalkorDB holds the highest throughput at every level and the lowest tail under
40-way load**, in both runs. With `THREAD_COUNT 1` it is effectively a
single-threaded event loop, so there is little contention and queries queue in a
predictable order instead of fighting over half a core. Single-threaded is an
advantage when there is less than one core to have.

**Neo4j is an order of magnitude down**, in both runs, with a p95 at 40 clients of
2.4 seconds.

**ArangoDB's collapse at 40 clients does not survive repetition.** Here it drops to
135.5 qps with a 905 ms p95; in the other run it held 430.6 qps. Same config. That is
a 218% spread and I am not going to draw a conclusion from it. It is the single
clearest example of why the variance report exists.

## 5. Footprint: durability costs far more than the graph does

| Engine | RSS | % of 256 MB | Store on disk | Durable here? |
| --- | ---: | ---: | ---: | :--: |
| Neo4j | 255.8 MiB | 99.9% | **542.5 MB** | yes |
| ArangoDB | 225.4 MiB | 88.0% | 87.8 MB | yes |
| Memgraph | 145.1 MiB | 56.7% | 0.2 MB | no |
| FalkorDB | 119.8 MiB | 46.8% | 0 MB | no |
| Kùzu | in-process | n/a | 9.3 MB | yes |

The graph itself is small: **Kùzu stores the whole thing, durably, in 9.3 MB**, and
FalkorDB reports 21 MB of Redis memory holding it. So Neo4j's 542 MB on disk is not
the graph, it is transaction logs, and it is **more than half the declared 1 GB disk
allowance consumed by logging one 198k-relationship load**. On a tier with a 1 GB
disk that is arguably a harder constraint than any latency number here, and it is the
kind of thing a latency-only benchmark misses entirely.

The two engines under 60% of the memory cap are exactly the two that are not durable
in this configuration. That is the largest asymmetry in the study. It is not that
Memgraph and FalkorDB are lean; they have been excused from a job the other two are
doing, and both of those choices (`--storage-wal-enabled=false`, `--save ''`) are
mine.

## 6. What the `RETURN 1` baseline bought

The baseline exists so a cloud-hosted CognoDB can be compared against loopback
containers. It has already paid for itself on the local track, and it is the only
metric that repeated within 6% on every engine.

| Engine | `RETURN 1` p50 | transport |
| --- | ---: | --- |
| Kùzu | 0.042 ms | none, in-process |
| FalkorDB | 0.248 ms | RESP |
| Memgraph | 0.318 ms | Bolt |
| ArangoDB | 0.933 ms | HTTP + VelocyPack |
| Neo4j | 2.993 ms | Bolt |

Kùzu's 0.042 ms is the floor: no socket, no serialisation, a function call. So
roughly 0.2-0.3 ms of every Bolt query here is protocol plus loopback, and
ArangoDB's HTTP transport costs about 0.9 ms before it does any work at all. Neo4j's
2.99 ms is *not* transport, since it speaks the same Bolt as Memgraph's 0.318 ms; it
is the GC story from section 3 appearing in the cheapest possible query.

Two things fall out of that:

**Some apparent engine wins are transport wins.** FalkorDB's 1-hop advantage over
ArangoDB is 0.41 ms against 1.32 ms, but 0.25 ms and 0.93 ms of those are the
baseline, so the engine-only difference is 0.16 ms against 0.39 ms. Real, but under
half the size the raw numbers suggest.

**And subtracting the baseline widens ArangoDB's 3-hop lead over Memgraph**, because
Memgraph starts from a cheaper transport: 4.94 ms of engine work against 13.56 ms.

## 7. What I would do differently

**Run on an idle machine.** This is the big one. A 364% spread on ArangoDB's ingest
rate between identical runs is not a property of ArangoDB, it is a property of my
laptop doing other things, and it undermines every throughput number in the study.
Everything else on this list is secondary to it.

**More than two runs.** Two runs give a range. Three to five would give a usable
distribution and let the report show error bars instead of a caveat. The harness
already supports it (`graphbench run` is idempotent and `graphbench compare` takes
any number of run ids), so this is wall-clock time rather than work.

**Report each engine at its own optimal batch size as well as the shared one.** One
value for everyone is right for fairness, but 1000 is Neo4j's worst and costs it 56%
against its own optimum, on the engine that was already struggling. The sweep data to
report both is already in [CALIBRATION.md](CALIBRATION.md).

**Close the traversal formulation gap.** The Cypher engines enumerate paths where
ArangoDB visits vertices, and that difference is doing real work in the 3-hop column.
A second, clearly-labelled variant using each engine's native BFS construct would
separate "this engine is slower" from "this query language made it do more work".

**Include hub nodes.** Start nodes are capped at degree 50, which was necessary to
stop 3-hop expansions from OOMing 256 MB instances. But degree 504 exists in this
graph, and *how* each engine fails on a hub is probably more useful to someone
choosing a database than how it performs on a median node.

**Use a dataset big enough to test what CognoDB is built for.** Its design point is a
disk-backed store with a hot cache traversing a graph larger than RAM. At 198,050
relationships nothing ever leaves memory, so this benchmark never asks the question
that design answers. A larger dataset would break Memgraph outright, which is exactly
why it would be worth running.
