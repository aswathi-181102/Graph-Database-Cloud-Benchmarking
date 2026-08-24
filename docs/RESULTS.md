# Results

Generated from `results/20260823T182707Z/` by `graphbench report`. Do not edit by hand.

## Run

- Run id: `20260823T182707Z`, started 2026-08-23T18:27:07+00:00, finished 2026-08-23T18:27:40+00:00
- Dataset: **ca-astroph**, 18,771 nodes / 198,050 relationships, sha256 `51bf1e2cace269b8...`
- Degree: min 1, mean 21.102, max 504. Start nodes: 256 from degree band unrecorded, seed 7919
- Source: https://snap.stanford.edu/data/ca-AstroPh.txt.gz
- Client: Darwin 25.5.0 / arm64, 8 cores, 9 GB RAM, Python 3.14.7
- Drivers: neo4j 5.28.4, FalkorDB 1.7.1, python-arango 8.3.3, kuzu not installed
- Code: commit `13514b0b1a71` (working tree dirty)
- Workload: 100 measured iterations after 20 warm-up, batch size 5000, per-query ceiling 30s
- Mixed: 90% read at [1, 10, 40] clients for 30s each (**skipped this run**)

## Verification

Only one platform answered, so there was nothing to cross-check. Agreement between platforms needs at least two.

## Platforms and tiers

| Platform | Track | Engine | Version | vCPU | RAM | Disk | Tier source |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| Memgraph (capped) | local | memgraph | Memgraph 3.12.0 | 0.5 | 256 MB | 1 GB | docker-compose.yml |

## Resource-matched track (primary)

Every engine in Docker at 0.5 vCPU / 256 MB, CognoDB's free c0 envelope, on one client machine over loopback. This is the only comparison here that can honestly claim resource parity.

### Ingest

| Platform | Nodes/s | Rels/s | Index build (s) | Total load (s) | Rows loaded | Load method |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Memgraph (capped) | 13,172 | 48,712 | 0.06 | 5.6 | 216,821/216,821 | official Neo4j Bolt driver, UNWIND batches |

### Read latency

| Platform | RETURN 1 | Point | 1-hop | 2-hop | 3-hop | Filtered | Group-by |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **P50 (ms)** |  |  |  |  |  |  |  |
| Memgraph (capped) | 0.37 | 0.67 | 0.54 | 1.05 | 14.02 | 2.08 | 7.54 |
| **P95 (ms)** |  |  |  |  |  |  |  |
| Memgraph (capped) | 0.81 | 1.34 | 1.75 | 2.77 | 169.21 | 47.44 | 40.31 |

A ⚠ marks a workload that was abandoned or had failed iterations. Its percentiles cover only the iterations that completed.

### Engine cost with the network subtracted

Traversal p50 minus the `RETURN 1` p50 on the same platform.

| Platform | RETURN 1 p50 | 1-hop minus baseline | 2-hop minus baseline | 3-hop minus baseline |
| --- | ---: | ---: | ---: | ---: |
| Memgraph (capped) | 0.37 | 0.16 | 0.68 | 13.64 |

### Mixed workload

_Mixed workload was not run._

### Footprint

| Platform | Observed RSS | % of cap | Store on disk | Engine-reported | Source |
| --- | ---: | ---: | ---: | --- | --- |
| Memgraph (capped) | 129.5MiB / 256MiB | 50.57% | 3.5 MB | yes | SHOW STORAGE INFO |

### Indexes actually created

**Memgraph (capped)**
- unique key: CREATE CONSTRAINT ON (a:Author) ASSERT a.key IS UNIQUE
- key: CREATE INDEX ON :Author(key)
- id (edge load path): CREATE INDEX ON :Author(id)
- cohort (no composite support): CREATE INDEX ON :Author(cohort)
- bench tag (mixed workload cleanup): CREATE INDEX ON :Bench(tag)

### Run status

| Platform | Status | Restarted first | Wall clock (s) | Errors |
| --- | --- | --- | ---: | ---: |
| Memgraph (capped) | ok | yes | 33 | 0 |

## Charts

![Traversal latency by depth](charts/traversal_latency.png)

![p50 against p95](charts/p50_vs_p95.png)

![Ingest throughput](charts/ingest_throughput.png)

## Errors

No platform reported an error on this run.

## Reading these numbers

- Percentiles are nearest-rank over the raw samples, which are kept in the results JSON so any of this can be recomputed or plotted differently.
- Latency is measured client-side and includes driver and protocol cost. The `RETURN 1` column is that cost with no graph work in it.
- `cpus: 0.5` on the local track is a CFS quota, a hard ceiling every period. CognoDB's 0.5 vCPU is burstable and can exceed baseline while it has credit, so tail latency is not directly comparable across tracks.
- Every judgement call behind these numbers, and what this suite does not measure, is in [DECISIONS.md](DECISIONS.md).

