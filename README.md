# Graph Database Cloud Benchmarking

Benchmarks **CognoDB Cloud** against **Neo4j**, **Memgraph**, **ArangoDB**,
**FalkorDB** and **Kùzu** on the same dataset, the same queries, the same client
machine, and the same amount of CPU and RAM.

The headline is not which database won. It is that at 0.5 vCPU / 256 MB, the
question "did it finish at all" turns out to matter more than any latency number,
and three separate attempts at this benchmark produced fast, clean, completely
wrong results before the correctness checks caught them.

- [Results](docs/RESULTS.md) - full matrix, charts, per-iteration samples, verbatim errors
- [Decisions](docs/DECISIONS.md) - every judgement call, and what this suite does not measure
- [Calibration](docs/CALIBRATION.md) - measured batch size sweep and repeat-run variance
- [Local stack](docs/LOCAL_STACK.md) - what each engine needed before it fit in 256 MB

## Why most graph benchmarks are not worth reading

Two failure modes account for almost all of them. Either the databases were not
given the same hardware, or the "same query" was not actually the same query. This
repo tries to close both, and then check that it succeeded rather than assume it.

**Same hardware.** The resource envelope is 0.5 vCPU / 256 MB RAM / 1 GB disk,
which is what CognoDB's free `c0` instance gets. Every engine in the primary track
runs in Docker capped to exactly that, from one shared YAML anchor so the caps
cannot drift apart.

**Same query.** Four of the five engines speak Cypher and run byte-identical query
text. Only the DDL differs, because index syntax is where the dialects genuinely
diverge, and each adapter reports what it actually managed to create. ArangoDB is
the exception and needed real care, covered below.

**And then verify it.** Every read operation returns a value, not just a duration.
The runner compares those values across platforms and exits non-zero if they
disagree, because a query that is accidentally cheaper on one engine is not a
performance win, it is a broken comparison. This is the check that caught the worst
bug in the project.

## Why these five comparators

CognoDB is not a general purpose enterprise graph database. It is a context graph
for AI agents: Bolt and Cypher compatible, disk backed with heavy caching, roughly
80 bytes per edge, built to run as small isolated instances per session. So the
comparators are chosen along the architectural axes that could actually explain a
difference against that design.

| Engine | Axis it represents |
| --- | --- |
| **Neo4j** 5.26 Community | native graph, disk backed, JVM. The reference implementation of Cypher and Bolt, which CognoDB claims drop-in driver compatibility with |
| **Memgraph** 3.12 | native graph, **in memory**, C++ |
| **ArangoDB** 3.12 | **non-native** graph on RocksDB, adjacency rebuilt from global indexes |
| **FalkorDB** 4.20 | Cypher over a **sparse matrix** (GraphBLAS) engine, and a direct GraphRAG rival |
| **Kùzu** | **embedded**, no server process. The closest analogue to one small instance per agent |

Memgraph and ArangoDB are in the set specifically because CognoDB's own site claims
both crashed under a 256 MB cap. That is a testable claim, and this is the envelope
to test it in.

Rejected, with reasons: **Neptune** (no free tier at all), **NebulaGraph** (three
separate processes, will not fit), **JanusGraph** (JVM plus a separate storage
backend), **TigerGraph** (GSQL, OLAP oriented, free tier has far more RAM),
**Dgraph** (not Cypher, free cloud tier gone). Full reasoning in
[DECISIONS.md](docs/DECISIONS.md#1-which-databases).

## Two tracks, never mixed into one ranking

Free cloud tiers are **not equal to each other**. Aura Free advertises 1 GB,
Memgraph Cloud 2 GB, against CognoDB's 256 MB, and none of them can be dialled
down to match. Presenting one combined league table across those would be exactly
the methodology error the brief warns about, so there are two tracks and they are
reported separately:

- **local** - Docker, every engine capped identically to CognoDB's `c0`, one client
  machine, loopback. **This is the resource-fair comparison and the primary result.**
- **cloud** - managed free tiers as they actually ship, specs recorded as
  advertised, labelled not resource equal. Kept because "what do you actually get
  from the free tier you would really sign up for" is a genuine question.
- **reference** - Kùzu, embedded, not ranked against anything.

CognoDB has no self-hosted build, so it can only appear in the cloud track. That is
the one unavoidable asymmetry in the study.

**Which is why `RETURN 1` is a measured workload.** CognoDB is reached over the
internet while the capped engines are on loopback. Without a no-op baseline, a
traversal comparison between them is partly a speed test of my broadband.
Subtracting the baseline leaves something much closer to the engine's own work, and
Kùzu anchors the zero-network end of the same scale.

## Dataset

SNAP **ca-AstroPh**, the arXiv astrophysics co-authorship network.
**18,771 nodes / 198,050 relationships** after dedupe, sha256 pinned in the
registry and re-verified on every fetch.

Chosen because it clears the 100k relationship floor, fits 256 MB with indexes, is
a 1.5 MB download so a reproduction costs seconds rather than an hour, and has
genuinely heavy-tailed degree (mean 21, max 504) which is what makes 2-hop and
3-hop diverge instead of all looking the same.

**Our counts differ from SNAP's published 18,772 / 198,110, on purpose.** There are
exactly 60 self loops, and the one extra node they count (id 64582) appears only in
a self loop. Verified against the raw file, not assumed. Self loops go because they
make "1-hop neighbours" ambiguous across engines.

SNAP edge lists have no attributes, so `key`, `cohort` and `degree` are **derived**
from the node id rather than randomly generated, which means every platform gets
byte-identical properties by construction and a stranger can reproduce them from
the raw file alone.

**Start nodes come from a degree band of 5 to 50, not uniform random.** A 3-hop
expansion from a degree-504 hub on a 256 MB instance measures the OOM killer, and
under uniform sampling p95 would mostly record whether a run happened to draw a
hub. This is a real limitation and not a free lunch: **these results describe
mid-degree traversals and deliberately exclude hub behaviour.**

## Metrics

Every metric is measured on every platform, 100 iterations after 20 discarded
warm-up iterations, reported as p50 and p95 nearest-rank with the raw samples kept
in the results JSON.

| Category | What is measured |
| --- | --- |
| Data loading | nodes/s, relationships/s, index build time, total wall clock, **and how many rows actually made it in** |
| Traversals | 1, 2 and 3-hop, defined as the k-hop neighbourhood, from 256 fixed start nodes |
| Lookups | point lookup on an indexed string key, plus an equality + range filtered lookup |
| Aggregations | group-by over a label (primary, cannot be served from metadata) and a relationship count (secondary, some engines answer from a counter) |
| Mixed | sustained qps at 1 / 10 / 40 clients, 90% read, reads split half point lookup half 2-hop |
| Footprint | container RSS, store size on disk, plus whatever the engine reports itself. "Not observable" where it is not |
| Baseline | `RETURN 1`, to separate protocol and network cost from engine cost |

## Results

<!-- BEGIN GENERATED RESULTS -->

Run `20260824T060418Z` on **ca-astroph** (18,771 nodes / 198,050 relationships). Regenerate with `make report`. Full detail, charts, per-iteration samples and verbatim errors: [docs/RESULTS.md](docs/RESULTS.md).

**Cross-checked:** every platform returned identical values on 404 query results, so the latencies below are comparing equivalent queries.

### Resource-matched track (primary)

Every engine in Docker at 0.5 vCPU / 256 MB, CognoDB's free c0 envelope, on one client machine over loopback. This is the only comparison here that can honestly claim resource parity.

**Tiers**

| Platform | Track | Engine | Version | vCPU | RAM | Disk | Tier source |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| ArangoDB (capped) | local | arangodb | ArangoDB 3.12.10-1 | 0.5 | 256 MB | 1 GB | docker-compose.yml |
| FalkorDB (capped) | local | falkordb | FalkorDB module 4.20.4 on Redis 8.6.3 | 0.5 | 256 MB | 1 GB | docker-compose.yml |
| Memgraph (capped) | local | memgraph | Memgraph 3.12.0 | 0.5 | 256 MB | 1 GB | docker-compose.yml |
| Neo4j 5 Community (capped) | local | neo4j | Neo4j Kernel 5.26.29 (community) | 0.5 | 256 MB | 1 GB | docker-compose.yml |

**Data loading**

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ArangoDB (capped) | 54,201 | 26,082 | 0.18 | 8.1 | 216,821/216,821 | python-arango, AQL batch INSERT (not import_bulk, see module docstring) |
| FalkorDB (capped) | 47,457 | 4,098 | 1.09 | 49.8 | 216,821/216,821 | FalkorDB client over RESP, GRAPH.QUERY with UNWIND batches |
| Memgraph (capped) | 32,060 | 69,651 | 0.20 | 3.6 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |
| Neo4j 5 Community (capped) | 2,026 | 2,686 | 18.73 | 101.7 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |

**Read latency (ms)**

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| ArangoDB (capped) | 0.88 | 1.07 | 1.82 | 1.85 | 7.62 | 1.50 | 5.55 |
| FalkorDB (capped) | 0.23 | 0.33 | 0.50 | 0.76 | 58.84 | 0.84 | 2.91 |
| Memgraph (capped) | 0.32 | 0.34 | 0.39 | 0.64 | 11.30 | 0.54 | 3.64 |
| Neo4j 5 Community (capped) | 2.93 | 4.40 | 5.94 | 6.48 | 15.20 | 2.38 | 51.59 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| ArangoDB (capped) | 1.72 | 2.05 | 4.57 | 3.98 | 65.94 | 2.22 | 35.43 |
| FalkorDB (capped) | 0.31 | 0.77 | 1.78 | 3.78 | 410.88 | 1.63 | 3.19 |
| Memgraph (capped) | 0.96 | 0.58 | 0.62 | 1.68 | 166.75 | 1.10 | 50.87 |
| Neo4j 5 Community (capped) | 68.21 | 79.32 | 68.17 | 71.38 | 115.06 | 66.40 | 109.90 |

**Mixed workload**

| Platform | 1 client (qps) | 10 clients (qps) | 40 clients (qps) | Read p95 @ max | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| ArangoDB (capped) | 524.0 | 527.1 | 430.6 | 196.67 | 50 |
| FalkorDB (capped) | 1048.6 | 842.9 | 929.0 | 85.46 | 0 |
| Memgraph (capped) | 692.0 | 429.9 | 534.3 | 113.00 | 0 |
| Neo4j 5 Community (capped) | 55.6 | 58.6 | 80.9 | 795.14 | 1 |

**Footprint**

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| ArangoDB (capped) | 254MiB / 256MiB | 99.21% | 88.5 MB | not observable | docker stats |
| FalkorDB (capped) | 128.4MiB / 256MiB | 50.17% | 0.0 MB | yes | INFO memory + GRAPH.MEMORY |
| Memgraph (capped) | 148.1MiB / 256MiB | 57.86% | 0.2 MB | yes | SHOW STORAGE INFO |
| Neo4j 5 Community (capped) | 255.9MiB / 256MiB | 99.97% | 542.5 MB | yes | dbms.queryJmx |

### Embedded reference (not ranked)

In-process, so there is no network round trip at all. Included as a floor: the gap between this and the Bolt engines is protocol and network cost rather than graph engine cost.

**Tiers**

| Platform | Track | Engine | Version | vCPU | RAM | Disk | Tier source |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| Kuzu (embedded, reference only) | reference | kuzu | Kuzu 0.11.3 (embedded, in-process) | - | 256 MB | 1 GB | graphbench/adapters/kuzu.py |

**Data loading**

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Kuzu (embedded, reference only) | 40,075 | 8,176 | 0.05 | 24.7 | 216,821/216,821 | embedded kuzu, in-process Cypher UNWIND batches |

**Read latency (ms)**

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| Kuzu (embedded, reference only) | 0.05 | 0.12 | 1.54 | 1.68 | 13.32 | 0.24 | 0.35 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| Kuzu (embedded, reference only) | 0.07 | 0.15 | 2.11 | 2.87 | 239.80 | 0.32 | 0.37 |

**Mixed workload**

| Platform | 1 client (qps) | 10 clients (qps) | 40 clients (qps) | Read p95 @ max | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kuzu (embedded, reference only) | 528.3 | 458.6 | 506.8 | 224.98 | 50 |

**Footprint**

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| Kuzu (embedded, reference only) | not observable | - | - | yes | store file size |

### Not run

No credentials configured, so these were skipped rather than failed:

- `cognodb-cloud`: missing COGNODB_URI, COGNODB_PASSWORD
- `neo4j-aura`: missing NEO4J_AURA_URI, NEO4J_AURA_PASSWORD
- `memgraph-cloud`: missing MEMGRAPH_CLOUD_URI, MEMGRAPH_CLOUD_PASSWORD
- `arango-cloud`: missing ARANGO_CLOUD_URL, ARANGO_CLOUD_PASSWORD
- `falkordb-cloud`: missing FALKORDB_CLOUD_HOST, FALKORDB_CLOUD_PASSWORD

<!-- END GENERATED RESULTS -->

## Three bugs that produced fast, clean, wrong numbers

Worth reading before trusting any benchmark, including this one.

**1. A wipe that killed the database.** Deleting the graph 10,000 nodes at a time
means detaching over 100,000 relationships in one transaction. Against Neo4j's
96 MB heap that is `OutOfMemoryError: Java heap space`, followed by a dropped
connection and every subsequent query failing on a defunct socket. The delete batch
is now adaptive and halves on a resource error, resetting the session first.

**2. A reload into memory that was never freed.** Memgraph loaded all 198,050
relationships, then failed at 5,000 on the next run in the same process:
`Memory limit exceeded! Current use is 202.86MiB`. `wipe()` had deleted the data
and reported success, but Memgraph does not return freed memory to its allocator,
so the reload started with the budget already spent. Left alone, this produced a
**3-hop p50 of 0.64 ms** - a spectacular-looking number, measured against a graph
holding 5,000 of 198,050 edges. With a clean store it is 14.02 ms.

**3. A restart that restored the problem.** So the runner started restarting
containers. Not enough: Memgraph writes a snapshot on exit even with periodic
snapshots and the WAL disabled, so the restarted container recovered the previous
run's graph and OOMed again at 15,000 nodes with zero relationships. The
cross-platform check caught this one - Memgraph answered 0 where the other three
answered 42 - and the run exited non-zero rather than publishing the table.

The store is now destroyed and rebuilt, volumes included, before every platform's
load. All three bugs shared one shape: **they made the numbers better, not worse.**
That is the direction benchmark bugs tend to run, which is the argument for
checking returned values and not just durations.

## ArangoDB needed the most care

It is the only non-Cypher engine, so its queries are hand-written, which is the
weakest link in the fairness argument. Cypher's variable-length patterns use
**relationship uniqueness** (a path cannot reuse an edge, but may revisit a node);
ArangoDB's traversal defaults to **path uniqueness**. Left alone the two engines
answer different questions and the counts do not match.

`OPTIONS {uniqueVertices: 'global', order: 'bfs'}` is what makes AQL return the
same set `count(DISTINCT b)` reduces to. That is semantic alignment, not tuning.
The evidence it worked is that Memgraph, FalkorDB and ArangoDB return identical
counts on the same start nodes:

| start node | 1-hop | 2-hop | 3-hop |
| --- | --- | --- | --- |
| a84 | 5 | 49 | 481 |
| a1250 | 30 | 795 | 8,140 |
| a1418 | 7 | 116 | 1,711 |

## Known unfairness, stated rather than hidden

None of these are worked around, because each one is a genuine property of the
engine rather than a trick, and hiding them would be worse than reporting them.

- **ArangoDB skips a lookup the others cannot.** It addresses documents by primary
  key, so `_from`/`_to` are built client-side and no endpoint lookup happens during
  edge loading. The Cypher engines must resolve both endpoints through a secondary
  index.
- **ArangoDB's ingest is understated.** It uses AQL batch `INSERT`, not
  `import_bulk`, because batch INSERT is the direct analogue of `UNWIND ... CREATE`.
  Its real ceiling is higher than reported here.
- **FalkorDB has no unique constraint** on the key, only an index, because its
  enforcement is a separate command not available on every build. It does slightly
  less work on the node phase than Neo4j.
- **Memgraph and FalkorDB both run non-durable.** Memgraph has snapshots and the
  WAL disabled, which is what Memgraph documents for query benchmarking; FalkorDB
  runs with `--save ''` so Redis never writes an RDB. Neo4j and ArangoDB are left
  durable. This is the largest asymmetry in the set and it shows up directly in the
  footprint table: FalkorDB writes 0 bytes to disk and Neo4j writes 542 MB.
- **Neo4j idles at 99% of its memory cap** on an empty database: 96 MB heap plus
  48 MB page cache plus JVM overhead. It has no headroom, so any Neo4j failure here
  is a failure at the cap.
- **`cpus: 0.5` is a CFS quota**, a hard ceiling every scheduling period. CognoDB's
  0.5 vCPU is burstable and can exceed baseline while it has credit, then throttle
  hard. Tail latency is not directly comparable across the two tracks.
- **Disk is declared, not enforced.** `storage_opt.size` needs a quota-capable
  storage driver and Docker Desktop's overlay2 on macOS is not one. It is also not
  binding at 2.7 MB of input.
- **Cloud platforms cannot be reset.** Their load numbers are measured against
  whatever state the managed service happened to be in.

## Running it

```bash
make setup                  # venv, exact pinned deps
cp .env.example .env        # fill in whatever credentials you have
make dataset                # download, verify sha256, write canonical CSV
make up                     # start the capped local stack, wait for healthy
make doctor                 # which platforms are reachable, which get skipped
make bench                  # run everything reachable
make report                 # regenerate tables, charts, and this README's matrix
```

`make doctor` exists so a typo in a password is found in one second rather than
three minutes into an ingest. A platform with no credentials is **skipped, not
failed** - nobody reproducing this will have accounts on all nine entries, so a
partial run is a normal outcome.

Credentials are read from the environment only. `config/platforms.yaml` holds
`${ENV_VAR}` references and is safe to commit.

Other things you can do:

```bash
graphbench run --track local            # just the resource-matched track
graphbench run --platforms cognodb-cloud,neo4j-aura
graphbench run --skip-mixed             # reads only, much faster
graphbench calibrate --repeats 3        # batch size sweep and variance
graphbench dataset info                 # provenance of the prepared graph
```

## Layout

```text
config/           platform and workload definitions, env refs only, no secrets
src/graphbench/
  datasets/       download, verify, sample, write canonical CSV
  adapters/       one per engine, one shared Cypher workload
  workloads/      reads and the concurrency sweep
  report/         markdown tables and charts, generated from the JSON
  verify.py       cross-platform agreement, known answers, monotonic hops
  dockerctl.py    destroy and rebuild the local stores
docker-compose.yml    the capped local stack
results/          raw run output, committed
docs/             results, decisions, calibration, local stack notes
tests/            unit tests for the pure logic
```

## What this benchmark does not measure

Stated plainly, because the gaps matter as much as the numbers.

Hub-node traversal (start nodes are capped at degree 50). Graphs larger than RAM,
which is the regime CognoDB's disk-backed design exists for and where it should
look best. Write-heavy workloads. Cold start from a stopped engine. Clustering,
replication or failover. Anything above the 256 MB tier.

And it does not reproduce CognoDB's published figures of 0.27 ms 2-hop and 74,000
reads/sec. Those are their dataset on their hardware; this is a different dataset
on a laptop over the public internet. Nothing here refutes them.

## License

MIT
