# Decisions

Every choice that could have gone another way, and why it went this way. Written
so the numbers can be defended, and so the things this benchmark deliberately
does not measure are on the record.

## 1. Which databases

CognoDB is not a general purpose enterprise graph database. It is a context graph
for AI agents: Bolt + Cypher, disk backed with heavy caching, roughly 80 bytes per
edge, built to run as small isolated instances per session. So comparators were
picked along the architectural axes that could explain a difference against that
design, not by popularity.

| Engine | Axis it represents |
|---|---|
| Neo4j | native graph, disk backed, JVM. The Cypher/Bolt reference implementation |
| Memgraph | native graph, in memory, C++ |
| ArangoDB | non-native graph on RocksDB, adjacency rebuilt from global indexes |
| FalkorDB | Cypher over a sparse matrix (GraphBLAS) engine, and a GraphRAG rival |
| Kùzu | embedded, no server process, closest analogue to one instance per agent |

Memgraph and ArangoDB are in the set specifically because CognoDB's own site
claims both crashed under a 256 MB cap. That is a testable claim, and this is the
envelope to test it in.

**Rejected:**

- **Amazon Neptune** - no free tier at all. Matching it to 512 MB means paying for
  an instance and then handicapping it, which is worse science than omitting it.
- **NebulaGraph** - needs metad, storaged and graphd as separate processes. They
  do not fit in 512 MB together, so any result measures the cap, not Nebula.
- **JanusGraph** - JVM plus a separate storage backend (Cassandra or BerkeleyDB).
  Same problem, worse.
- **TigerGraph** - GSQL not Cypher, tuned for OLAP fan-out, and the free tier
  ships far more than 512 MB. Would need either an unfair tier or a rewritten
  workload.
- **Dgraph** - not Cypher, and the free cloud tier is gone.

## 2. Two tracks, never mixed in one ranking

The brief says every database must get equivalent vCPU/RAM/storage and warns that
comparing unequal tiers is a methodology error. The problem: free cloud tiers are
not equal to each other. Aura Free ships 1 GB, Memgraph Cloud 2 GB, and none of
them can be dialled down to CognoDB's 0.5 vCPU / 512 MB.

So there are two tracks and they are never combined into one league table:

- **local** - Docker containers, all capped identically to CognoDB's c0. This is
  the resource fair comparison and the primary result.
- **cloud** - managed free tiers as they actually ship, specs recorded as
  advertised, explicitly labelled not resource equal.
- **reference** - Kùzu, embedded, not ranked at all.

CognoDB has no self-hosted build, so it can only appear in the cloud track. That
is the one unavoidable asymmetry in the study.

### Which is why `RETURN 1` is a measured workload

CognoDB is a managed service reached over the internet. The capped engines are on
loopback. Comparing their 2-hop latency directly would partly be measuring my
broadband. So `RETURN 1` is measured on every platform as a first class workload:
it exercises driver, protocol and network with zero graph work, so subtracting it
from a traversal number leaves the engine's own cost. Kùzu, being in-process,
anchors the zero-network end of the same scale.

## 3. Dataset: SNAP ca-AstroPh

Requirements it had to meet: ≥100k relationships, fits 512 MB with indexes,
public and citable, real degree skew.

18,771 nodes / 198,050 edges after dedupe. Sits inside the suggested 100k-500k
band, 1.5 MB download so a reproduction costs seconds, and the degree
distribution is genuinely heavy tailed (mean 21, max 504) which is what makes
2-hop and 3-hop diverge instead of all looking the same. Being co-authorship it is
naturally undirected, so one Cypher pattern works without direction fudging.

**Rejected:**

- **soc-Pokec**, the brief's own example. 30M edges, 400 MB download. Usable only
  after aggressive sampling, at which point sampling strategy becomes the single
  biggest threat to fairness in the study. Kept in the registry as a sampled
  option, with a working sampler, so the claim "we could have used it" is backed
  by code.
- **Neo4j movies graph** - ~250 relationships. Three orders of magnitude short.
- **LDBC SNB** - the academically correct answer. Even SF1 overshoots 512 MB, and
  the generator is a JVM/Spark toolchain that would wreck the clone-and-rerun
  property this repo is graded on.
- **cit-HepPh** (421k edges) - a real contender and would have been valid. Passed
  on because at 421k edges plus indexes there is a genuine risk of hitting the
  512 MB ceiling on some engines, turning "we measured latency" into "we measured
  which engine OOMs first". Interesting, but a different experiment.

### Our counts differ from SNAP's published figures, on purpose

SNAP advertises 18,772 nodes / 198,110 edges. We measure 18,771 / 198,050.
Verified against the raw file rather than assumed: there are exactly 60 self
loops, and the one extra node they count (id 64582) appears only in a self loop.
Self loops are dropped because they make "1-hop neighbours" ambiguous - some
engines return the start node, some do not, and the resulting mismatch looks like
a correctness bug when it is a data artifact.

### Properties are derived, not generated

SNAP edge lists have no attributes, but the brief needs a point lookup, a filtered
lookup and a group-by. So `key`, `cohort` and `degree` are derived from the node
id.

Derived rather than randomly generated matters: a stranger re-running gets
byte-identical properties from the raw file alone, with no extra artifact to
download and no RNG state to agree on. And the properties are identical on all
platforms by construction rather than by careful copying.

`cohort` uses crc32, not Python's `hash()`. `hash()` on a str is salted per
process unless `PYTHONHASHSEED` is set, so it would hand different runs different
cohort assignments and silently break the group-by comparison.

32 cohorts is a compromise. One bucket per node degenerates into "return every
row" and measures result streaming. Two buckets is basically a count. 32 over
18.7k nodes gives ~590 each, which is a realistic group-by shape and still forces
a full label scan.

### Start nodes come from a degree band (5-50), not uniform random

Max degree here is 504. A 3-hop expansion from a hub reaches most of the graph,
which on a 512 MB instance measures the OOM killer rather than the traversal.
Worse, under uniform sampling p95 would largely record whether that run happened
to draw a hub, so the number would move between platforms for reasons unrelated
to the platform.

**This is a real limitation, not a free lunch.** These results describe mid-degree
traversals and deliberately exclude hub behaviour. Hub traversal is a legitimate
thing to want to measure and this suite does not measure it.

256 start nodes so a 100-iteration run never reuses one.

### Sampling strategy, for when the graph is too big

Random edge sampling shreds local structure: keep 1% of edges and you get mostly
degree-1 nodes, so 2-hop and 3-hop collapse toward the same tiny result and the
metric stops discriminating. Random node sampling with induced edges is better but
still fragments a heavy-tailed graph. Breadth-first snowball keeps one dense
connected region intact so k-hop still grows the way it does in the real graph.

Known bias: snowball over-represents high-degree nodes, since they get reached
early. Stated rather than glossed over. The best mitigation is that the default
dataset needs no sampling at all.

## 4. Traversal is defined as k-hop neighbourhood, not exact depth

"1-hop, 2-hop, 3-hop" is implemented as *distinct nodes within 1..k hops of the
start, excluding the start*, not *nodes at exactly depth k*.

This is forced by cross-engine comparability. Cypher's variable-length patterns
use **relationship uniqueness** (a path cannot reuse an edge, but may revisit a
node). ArangoDB's traversal defaults to **path uniqueness**. An exact-depth query
therefore returns genuinely different sets on the two engines and the counts would
not match.

The 1..k neighbourhood is the one definition both express exactly:

- Cypher: `MATCH (a {key:$key})-[:R*1..k]-(b) WHERE b.key <> $key RETURN count(DISTINCT b)`
- AQL: `FOR v IN 1..k ANY @start R OPTIONS {uniqueVertices:'global', order:'bfs'} COLLECT WITH COUNT INTO n RETURN n`

The ArangoDB options are **semantic alignment, not tuning**. Without them the two
engines answer different questions.

Useful side effect: the definition is monotonic, so 1-hop ≤ 2-hop ≤ 3-hop is a
free correctness check on every platform.

**Verified.** Memgraph, FalkorDB and ArangoDB return identical counts on the same
start nodes:

| start | 1-hop | 2-hop | 3-hop |
|---|---|---|---|
| a84 | 5 | 49 | 481 |
| a1250 | 30 | 795 | 8,140 |
| a1418 | 7 | 116 | 1,711 |

Engine-specific traversal accelerators (Memgraph's `*BFS`, for example) were
deliberately not used, because tuning one engine's query is exactly what makes
vendor benchmarks worthless.

## 5. Load path

Sequence is wipe → nodes → indexes → edges, identical on every engine, and
`load()` is a template method so no adapter can reorder it.

Indexes go between the two data phases. Before the nodes, a unique constraint
slows every insert and the node rate partly measures index maintenance. After the
edges, the edge phase has to find endpoints by full scan and takes minutes
everywhere. Between is both fastest and most realistic, and above all it is the
same order everywhere.

Neo4j needs `CALL db.awaitIndexes()` because it builds indexes asynchronously.
Without the wait, its index phase would look instant and the cost would leak into
the edge phase, flattering one number and penalising the other. Every other engine
here builds synchronously.

`CREATE`, not `MERGE`. The dataset is already deduplicated, so MERGE would add an
index probe per row for a guarantee we already have, and would penalise engines
with slower index lookups during a phase meant to measure write throughput.

Batch size is one value for every platform, including engines where a larger batch
would be faster. Letting each engine use its own optimum produces better numbers
and a worse benchmark.

### Known unfairness in the load path, stated rather than hidden

- **ArangoDB addresses documents by primary key**, so `_from`/`_to` are built
  client-side as strings and no endpoint lookup happens at all. The Cypher engines
  must look both endpoints up through a secondary index. This is a real ArangoDB
  advantage on ingest and it is not normalised away, because it is not a trick, it
  is what the data model buys.
- **ArangoDB uses AQL batch INSERT, not `import_bulk`.** import_bulk would likely
  be faster, but batch INSERT is the direct analogue of `UNWIND ... CREATE`: same
  batch size, same round trips, same server-side loop. This understates ArangoDB's
  real ingest ceiling.
- **FalkorDB has no unique constraint on `key`**, only an index, because its
  uniqueness enforcement is a separate command not available on every build. So it
  does slightly less work than Neo4j on the node phase.
- **Memgraph and FalkorDB both run non-durable.** Memgraph has
  `--storage-wal-enabled=false` and no snapshots, which is what Memgraph documents
  for query benchmarking. FalkorDB runs with `--save ''`, so Redis never writes an
  RDB. Neo4j and ArangoDB are left durable.

  This is the largest single asymmetry in the study and it is visible in the
  footprint table rather than only described here: FalkorDB writes 0 bytes to disk
  and Neo4j writes 542 MB, over half of the declared 1 GB allowance, most of it
  transaction logs. Two of these engines are being asked to guarantee durability
  and two are not, and that buys the non-durable pair both write throughput and
  disk.

## 6. Percentiles, not averages

On a burstable 0.5 vCPU instance the latency distribution is nowhere near normal:
most queries land in a tight band and a few get caught behind CPU throttling or a
GC pause and take twenty times longer. A mean folds those together and describes
neither case. p50 is what a query usually costs, p95 is what the bad ones cost,
and the gap between them is the interesting part.

Nearest-rank, no interpolation, so p95 of 100 samples is literally the 95th
smallest observation - something that actually happened, rather than a weighted
average of two things that did. Same convention as HdrHistogram. The cost is
resolution: with 100 samples the granularity of p95 is one sample, which is why
the iteration count is not lower.

p99 is reported but not leaned on; with 100 samples it is a single observation.

**Raw samples are kept in the results JSON**, not just the summary, so anyone can
recompute a different percentile, plot a histogram, or check that a p95 is not
being driven by one absurd outlier. A benchmark that only publishes summary
statistics cannot be argued with, which is not a virtue.

## 7. Warm-up and cold start

20 discarded warm-up iterations, then 100 measured. Cold and warm are different
questions and mixing them into one average answers neither.

The first warm-up call is kept separately as `first_call_ms`. It is **not a true
cold start** - the engine has just finished ingesting the data, so its caches are
hot from writing. It captures plan compilation and index warm-up only, and is
labelled that way. A true cold start requires restarting the engine.

## 8. Mixed workload

- **Sweep, not a single number** (1 / 10 / 40 clients). An engine that wins at one
  client and collapses at forty is a different product from one that stays flat,
  and one figure hides that. 40 clients against 0.5 vCPU is deliberate overload
  and should look like overload.
- **90/10 read/write.** Agent context graphs are read dominated: the agent reads
  its memory constantly and appends occasionally. 50/50 would be more stressful
  and less representative.
- **Reads are half point lookup, half 2-hop.** Point-only makes it an index
  benchmark; traversal-only lets one expensive query per client drop throughput to
  single digits on half a core, where differences drown in noise.
- **Seeded RNG per worker**, so the read/write interleaving is identical across
  platforms and reruns. Otherwise a lucky run with fewer writes posts a better
  number.
- **Threads, not processes.** These are network round trips, so the client sits
  blocked on a socket with the GIL released. An 8-core client driving a 0.5-core
  database is not the bottleneck, which is the property that has to hold for the
  throughput number to mean anything.
- **Workers merge results once at the end.** A shared lock in the hot loop would
  serialise the clients and the number would describe my mutex.
- **A barrier synchronises the start**, so the first thread does not get an
  uncontended database while the last thread gets a saturated one.
- Writes are tagged with the run id and deleted afterwards, so one platform's
  mixed run does not leave the graph different for the next.

## 9. Failure handling

Failures are counted, never raised past the workload. "17 of 100 iterations failed
with OOM" is a far more useful row in a results table than a traceback and no
data. Timeouts are recorded as timeouts, not folded into the percentiles as
ordinary samples.

A workload is abandoned rather than retried when the engine reports out of memory
or a dead connection, because at that point it cannot give meaningful timings and
continuing would fill the percentiles with retry noise. Anything softer is counted
and the loop continues.

`wipe()` is adaptive because of a real failure: at 10,000 nodes per
`DETACH DELETE` batch, Neo4j died with
`Neo.TransientError.General.OutOfMemoryError: Java heap space` and dropped the
connection - deleting 10,000 nodes means detaching over 100k relationships in one
transaction against a 96 MB heap. It now starts at 2,000 and halves to a floor of
100 on a resource error, resetting the session first because the OOM kills the
connection too. Wipe is not a measured phase, so a smaller batch costs only wall
clock.

## 10. Configuration

Credentials are `${ENV_VAR}` references resolved at load time, so
`config/platforms.yaml` is safe to commit and the brief's rule about not
committing URIs or passwords is structurally enforced rather than remembered.

A platform with missing variables becomes `skipped: missing COGNODB_URI` rather
than a stack trace on connect, because nobody reproducing this will have accounts
on all nine entries, so a partial run has to be a normal outcome.

Empty counts as missing. `COGNODB_URI=` is what you get from copying
`.env.example` and not filling it in, and treating that as configured means a
confusing connection failure later instead of a clear skip now.

`benchpass123` in `docker-compose.yml` is a throwaway password for containers
bound to localhost that `make down` destroys. It is not the same class of thing as
the cloud credentials, which are never in this repo.

## 11. Dependency pins

Exact `==` pins. Latency numbers are only comparable across runs if the client
driver stack does not move underneath them. Image tags are pinned to exact patch
versions for the same reason: a benchmark that says "Memgraph" without saying
which build cannot be repeated.

## 12. What this benchmark does not measure

- Hub-node traversal (start nodes are capped at degree 50)
- Graphs larger than RAM, which is the regime CognoDB's disk-backed design is
  built for and where it should look best
- Write-heavy workloads (the mix is 90/10)
- Cold start from a stopped engine
- Clustering, replication or failover
- Anything above the 512 MB tier
