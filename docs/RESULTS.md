# Results

Generated from `results/20260825T072533Z/` by `graphbench report`. Do not edit by hand.

## Run

- Run id: `20260825T072533Z`, started 2026-08-25T07:25:33+00:00, finished 2026-08-25T07:48:42+00:00
- Dataset: **ca-astroph**, 18,771 nodes / 198,050 relationships, sha256 `51bf1e2cace269b8...`
- Degree: min 1, mean 21.102, max 504. Start nodes: 256 from degree band [5, 50], seed 7919
- Source: https://snap.stanford.edu/data/ca-AstroPh.txt.gz
- Client: Darwin 25.5.0 / arm64, 8 cores, 9 GB RAM, Python 3.13.15
- Drivers: neo4j 5.28.4, FalkorDB 1.7.1, python-arango 8.3.3, kuzu 0.11.3
- Code: commit `5e22d75cefe6` (working tree dirty)
- Workload: 100 measured iterations after 20 warm-up, batch size 1000, per-query ceiling 30s
- Mixed: 90% read at [1, 10, 40] clients for 30s each

## Verification

All platforms returned identical values on **404** checked query results. Same input, same answer, so the latency comparison is between equivalent queries.

## Platforms and tiers

| Platform | Track | Engine | Version | vCPU | RAM | Disk | Tier source |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| ArangoDB (capped) | local | arangodb | ArangoDB 3.12.10-1 | 0.5 | 512 MB | 1 GB | docker-compose.yml |
| CognoDB Cloud (c0 free) | cloud | cognodb | CognoDB v0.9.11 (declared in console, no version procedure on the wire) | 0.5 | 512 MB | 1 GB | https://console.cognodb.com (provisioned instance, 2026-08-24) |
| FalkorDB (capped) | local | falkordb | FalkorDB module 4.20.4 on Redis 8.6.3 | 0.5 | 512 MB | 1 GB | docker-compose.yml |
| Kuzu (embedded, reference only) | reference | kuzu | Kuzu 0.11.3 (embedded, in-process) | - | 512 MB | 1 GB | graphbench/adapters/kuzu.py |
| Memgraph (capped) | local | memgraph | Memgraph 3.12.0 | 0.5 | 512 MB | 1 GB | docker-compose.yml |
| Neo4j 5 Community (capped) | local | neo4j | Neo4j Kernel 5.26.29 (community) | 0.5 | 512 MB | 1 GB | docker-compose.yml |

### Not run

- `neo4j-aura`: missing NEO4J_AURA_URI, NEO4J_AURA_PASSWORD
- `memgraph-cloud`: missing MEMGRAPH_CLOUD_URI, MEMGRAPH_CLOUD_PASSWORD
- `arango-cloud`: missing ARANGO_CLOUD_URL, ARANGO_CLOUD_PASSWORD
- `falkordb-cloud`: missing FALKORDB_CLOUD_HOST, FALKORDB_CLOUD_PASSWORD

A skipped platform means no credentials were configured, not that it failed.

## Resource-matched track (primary)

Every engine in Docker at 0.5 vCPU / 256 MB, CognoDB's free c0 envelope, on one client machine over loopback. This is the only comparison here that can honestly claim resource parity.

### Ingest

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ArangoDB (capped) | 57,076 | 23,079 | 0.16 | 9.1 | 216,821/216,821 | python-arango, AQL batch INSERT (not import_bulk, see module docstring) |
| FalkorDB (capped) | 140,082 | 4,770 | 0.18 | 41.8 | 216,821/216,821 | FalkorDB client over RESP, GRAPH.QUERY with UNWIND batches |
| Memgraph (capped) | 35,714 | 73,491 | 0.07 | 3.3 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |
| Neo4j 5 Community (capped) | 838 | 4,016 | 11.99 | 83.7 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |

### Read latency

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| ArangoDB (capped) | 1.61 | 1.16 | 1.37 | 1.65 | 6.76 | 1.21 | 5.28 |
| FalkorDB (capped) | 0.40 | 0.42 | 0.49 | 0.88 | 58.06 | 0.85 | 2.90 |
| Memgraph (capped) | 0.45 | 0.47 | 0.47 | 0.67 | 11.84 | 0.92 | 4.44 |
| Neo4j 5 Community (capped) | 3.94 | 4.18 | 2.68 | 5.72 | 7.51 | 2.82 | 10.95 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| ArangoDB (capped) | 14.88 | 2.24 | 2.76 | 3.10 | 64.80 | 1.98 | 37.80 |
| FalkorDB (capped) | 0.49 | 0.51 | 0.72 | 2.92 | 413.66 | 1.05 | 4.67 |
| Memgraph (capped) | 0.64 | 0.65 | 0.80 | 1.96 | 143.31 | 2.36 | 48.40 |
| Neo4j 5 Community (capped) | 73.55 | 92.18 | 75.64 | 86.90 | 94.75 | 74.30 | 97.22 |

A ⚠ marks a workload that was abandoned or had failed iterations. Its percentiles cover only the iterations that completed.

### Engine cost with the network subtracted

Traversal p50 minus the `RETURN 1` p50 on the same platform.

| Platform | RETURN 1 p50 | 1-hop minus baseline | 2-hop minus baseline | 3-hop minus baseline |
| --- | ---: | ---: | ---: | ---: |
| ArangoDB (capped) | 1.61 | 0.00 | 0.04 | 5.15 |
| FalkorDB (capped) | 0.40 | 0.08 | 0.47 | 57.66 |
| Memgraph (capped) | 0.45 | 0.03 | 0.22 | 11.39 |
| Neo4j 5 Community (capped) | 3.94 | 0.00 | 1.78 | 3.57 |

### Mixed workload

| Platform | 1 client (qps) | 10 clients (qps) | 40 clients (qps) | Read p95 @ max | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| ArangoDB (capped) | 627.3 | 654.2 | 459.7 | 193.60 | 50 |
| FalkorDB (capped) | 939.8 | 880.9 | 839.3 | 90.43 | 0 |
| Memgraph (capped) | 570.9 | 359.6 | 433.2 | 188.80 | 0 |
| Neo4j 5 Community (capped) | 175.5 | 85.5 | 85.2 | 1000.97 | 1 |

### Footprint

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| ArangoDB (capped) | 420.2MiB / 512MiB | 82.08% | 75.4 MB | yes | collection statistics() |
| FalkorDB (capped) | 115.5MiB / 512MiB | 22.55% | - | yes | INFO memory + GRAPH.MEMORY |
| Memgraph (capped) | 145.5MiB / 512MiB | 28.41% | 0.2 MB | yes | SHOW STORAGE INFO |
| Neo4j 5 Community (capped) | 508.4MiB / 512MiB | 99.29% | 542.5 MB | yes | dbms.queryJmx |

### Indexes actually created

**ArangoDB (capped)**
- unique key (persistent) on authors
- composite (cohort, degree) (persistent) on authors
- bench tag (persistent) on bench
- bench edge tag (persistent) on bench_edge
- NOTE: no secondary index on id, edge load uses _key via the primary index

**FalkorDB (capped)**
- key: CREATE INDEX FOR (a:Author) ON (a.key)
- id (edge load path): CREATE INDEX FOR (a:Author) ON (a.id)
- composite (cohort, degree): CREATE INDEX FOR (a:Author) ON (a.cohort, a.degree)
- bench tag (mixed workload cleanup): CREATE INDEX FOR (b:Bench) ON (b.tag)
- NOTE: no unique constraint on key, index only

**Memgraph (capped)**
- unique key: CREATE CONSTRAINT ON (a:Author) ASSERT a.key IS UNIQUE
- key: CREATE INDEX ON :Author(key)
- id (edge load path): CREATE INDEX ON :Author(id)
- cohort (no composite support): CREATE INDEX ON :Author(cohort)
- bench tag (mixed workload cleanup): CREATE INDEX ON :Bench(tag)

**Neo4j 5 Community (capped)**
- unique key: CREATE CONSTRAINT author_key IF NOT EXISTS FOR (a:Author) REQUIRE a.key IS UNIQUE
- id (edge load path): CREATE INDEX author_id IF NOT EXISTS FOR (a:Author) ON (a.id)
- composite (cohort, degree): CREATE INDEX author_cohort_degree IF NOT EXISTS FOR (a:Author) ON (a.cohort, a.degree)
- bench tag (mixed workload cleanup): CREATE INDEX bench_tag IF NOT EXISTS FOR (b:Bench) ON (b.tag)
- CONFIRMED constraint author_key: ['Author']['key'] UNIQUENESS
- CONFIRMED index author_cohort_degree: ['Author']['cohort', 'degree'] RANGE
- CONFIRMED index author_id: ['Author']['id'] RANGE
- CONFIRMED index author_key: ['Author']['key'] RANGE
- CONFIRMED index bench_tag: ['Bench']['tag'] RANGE
- CONFIRMED index index_343aff4e: ?? LOOKUP
- CONFIRMED index index_f7700477: ?? LOOKUP

### Run status

| Platform | Status | Store reset first | Wall clock (s) | Errors |
| --- | --- | --- | ---: | ---: |
| ArangoDB (capped) | ok | yes, volumes wiped | 124 | 0 |
| FalkorDB (capped) | ok | yes, volumes wiped | 155 | 0 |
| Memgraph (capped) | ok | yes, volumes wiped | 116 | 0 |
| Neo4j 5 Community (capped) | ok | yes, volumes wiped | 306 | 0 |

## Managed free tier track (not resource-matched)

Free tiers exactly as they ship. These tiers are **not equal to each other** (Aura Free advertises 1 GB, Memgraph Cloud 2 GB against CognoDB's 256 MB), so this table answers "what do you get from the free tier you would actually sign up for" and nothing stronger. Ranking these against the capped track would be a methodology error.

### Ingest

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| CognoDB Cloud (c0 free) | 2,795 | 3,258 | 1.99 | 69.5 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |

### Read latency

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| CognoDB Cloud (c0 free) | 303.97 | 296.53 | 304.26 | 284.65 | 862.43 ⚠ | 303.33 ⚠ | 292.01 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| CognoDB Cloud (c0 free) | 345.93 | 351.07 | 319.75 | 309.63 | 13830.29 ⚠ | 345.50 ⚠ | 351.44 |

A ⚠ marks a workload that was abandoned or had failed iterations. Its percentiles cover only the iterations that completed.

### Engine cost with the network subtracted

Traversal p50 minus the `RETURN 1` p50 on the same platform.

| Platform | RETURN 1 p50 | 1-hop minus baseline | 2-hop minus baseline | 3-hop minus baseline |
| --- | ---: | ---: | ---: | ---: |
| CognoDB Cloud (c0 free) | 303.97 | 0.30 | 0.00 | 558.46 |

### Mixed workload

| Platform | 1 client (qps) | 10 clients (qps) | 40 clients (qps) | Read p95 @ max | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| CognoDB Cloud (c0 free) | 3.3 | 37.9 | 88.6 | 813.05 | 0 |

### Footprint

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| CognoDB Cloud (c0 free) | not observable | - | - | not observable | - |

### Indexes actually created

**CognoDB Cloud (c0 free)**
- unique key: CREATE CONSTRAINT author_key IF NOT EXISTS FOR (a:Author) REQUIRE a.key IS UNIQUE
- id (edge load path): CREATE INDEX author_id IF NOT EXISTS FOR (a:Author) ON (a.id)
- cohort / composite: CREATE INDEX author_cohort_degree IF NOT EXISTS FOR (a:Author) ON (a.cohort, a.degree)
- bench tag (mixed workload cleanup): CREATE INDEX bench_tag IF NOT EXISTS FOR (b:Bench) ON (b.tag)
- CONFIRMED constraint author_key: Authorkey UNIQUE
- CONFIRMED index author_cohort_degree: Authorcohort, degree RANGE
- CONFIRMED index author_id: Authorid RANGE
- CONFIRMED index bench_tag: Benchtag RANGE

### Run status

| Platform | Status | Store reset first | Wall clock (s) | Errors |
| --- | --- | --- | ---: | ---: |
| CognoDB Cloud (c0 free) | partial | not a local container platform | 557 | 0 |

## Embedded reference (not ranked)

In-process, so there is no network round trip at all. Included as a floor: the gap between this and the Bolt engines is protocol and network cost rather than graph engine cost.

### Ingest

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Kuzu (embedded, reference only) | 45,888 | 8,114 | 0.03 | 24.8 | 216,821/216,821 | embedded kuzu, in-process Cypher UNWIND batches |

### Read latency

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| Kuzu (embedded, reference only) | 0.04 | 0.11 | 1.60 | 1.79 | 16.31 | 0.25 | 0.37 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| Kuzu (embedded, reference only) | 0.06 | 0.13 | 2.07 | 3.13 | 302.35 | 0.36 | 0.41 |

A ⚠ marks a workload that was abandoned or had failed iterations. Its percentiles cover only the iterations that completed.

### Engine cost with the network subtracted

Traversal p50 minus the `RETURN 1` p50 on the same platform.

| Platform | RETURN 1 p50 | 1-hop minus baseline | 2-hop minus baseline | 3-hop minus baseline |
| --- | ---: | ---: | ---: | ---: |
| Kuzu (embedded, reference only) | 0.04 | 1.55 | 1.75 | 16.27 |

### Mixed workload

| Platform | 1 client (qps) | 10 clients (qps) | 40 clients (qps) | Read p95 @ max | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kuzu (embedded, reference only) | 499.7 | 509.8 | 510.1 | 228.80 | 50 |

### Footprint

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| Kuzu (embedded, reference only) | not observable | - | 9.3 MB | yes | store file size |

### Indexes actually created

**Kuzu (embedded, reference only)**
- node table with primary key on key: CREATE NODE TABLE Author(id INT64, key STRING, cohort INT64, degree INT64, PRIMARY KEY(key))
- relationship table: CREATE REL TABLE Coauthor(FROM Author TO Author)
- bench node table (mixed workload): CREATE NODE TABLE Bench(key STRING, tag STRING, PRIMARY KEY(key))
- bench rel table (mixed workload): CREATE REL TABLE BenchEdge(FROM Author TO Bench)
- NOTE: only the primary key is indexed, id lookups are scans

### Run status

| Platform | Status | Store reset first | Wall clock (s) | Errors |
| --- | --- | --- | ---: | ---: |
| Kuzu (embedded, reference only) | ok | not a local container platform | 131 | 0 |

## Charts

![Traversal latency by depth](charts/traversal_latency.png)

![p50 against p95](charts/p50_vs_p95.png)

![Ingest throughput](charts/ingest_throughput.png)

![Concurrency sweep](charts/concurrency_sweep.png)

## Errors

No platform reported an error on this run.

## Reading these numbers

- Percentiles are nearest-rank over the raw samples, which are kept in the results JSON so any of this can be recomputed or plotted differently.
- Latency is measured client-side and includes driver and protocol cost. The `RETURN 1` column is that cost with no graph work in it.
- `cpus: 0.5` on the local track is a CFS quota, a hard ceiling every period. CognoDB's 0.5 vCPU is burstable and can exceed baseline while it has credit, so tail latency is not directly comparable across tracks.
- Two runs of identical configuration disagree by 60-360% on ingest and saturated throughput while agreeing within 6% on `RETURN 1`. Which metrics survive repetition is in [VARIANCE.md](VARIANCE.md), and any difference smaller than the spread there is not a finding.
- Interpretation, mechanisms and known gaps: [ANALYSIS.md](ANALYSIS.md).
- Every judgement call, and what this suite does not measure: [DECISIONS.md](DECISIONS.md).

