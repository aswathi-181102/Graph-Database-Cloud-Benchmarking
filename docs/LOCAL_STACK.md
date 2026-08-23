# The capped local stack

Notes from getting four engines to run inside CognoDB's free-tier envelope
(0.5 vCPU, 256 MB RAM). Kept because the tuning is part of the methodology: if
an engine only fits after being configured down, the configuration is a result,
not an implementation detail.

Host: Apple M-series, 8 cores, 8 GB RAM, macOS 26.5, Docker 29.7.2 (Docker
Desktop, aarch64). Every container gets `cpus: 0.5` and `mem_limit: 256m` from
one shared YAML anchor.

## Does everything even boot?

Yes, all four. Worth stating plainly because CognoDB's own marketing says
Memgraph and ArangoDB crashed under a 256 MB cap, and at least at boot they do
not. Whether they survive ingesting 198k relationships is a separate question
and the thing the actual benchmark answers.

Idle RSS measured with `docker stats --no-stream` after the container reported
healthy and before any data was loaded:

| Engine | Version | Idle RSS | % of 256 MB | Boot time to healthy |
|---|---|---|---|---|
| Memgraph | 3.12.0 | 72.8 MiB | 28% | ~15 s |
| FalkorDB | v4.20.4 | 79.8 MiB | 31% | ~10 s |
| ArangoDB | 3.12.10-1 | 137.9 MiB | 54% | ~75 s |
| Neo4j | 5.26.29 Community | 253.6 MiB | **99%** | ~30 s |

Neo4j sitting at 99% of its limit with an empty database is the single most
important number on this page. It has 96 MB of heap and 48 MB of page cache and
the rest is JVM overhead. There is essentially no headroom, so if it fails
later it will fail at the cap rather than for any graph-specific reason. Any
Neo4j number in the results has to be read with that in mind.

## What each engine needed before it would fit

None of them fit on defaults, because all of them size themselves from host RAM
rather than from the cgroup limit. That is the common thread and it is worth
saying out loud: on a 256 MB container with 8 GB visible on the host, every one
of these engines guesses wrong in the same direction.

**Neo4j** computes heap and page cache from host RAM, so it asked for roughly a
2 GB heap and was killed before it logged anything. Fixed with an explicit
96 MB heap and 48 MB page cache. Separately, it exited 1 on a config error
before that even mattered: `server.metrics.enabled` is an Enterprise-only
setting and Community refuses to start on any unrecognised key. Removed.

**ArangoDB** would not accept the tuning I wanted. `--rocksdb.total-write-buffer-size=33554432`
(32 MiB) is rejected outright with `invalid value`, and 64 MiB is the smallest
value it will take. So ArangoDB cannot be tuned as tightly as the others, which
is a real constraint at this tier rather than a preference of mine.

It also set `--query.memory-limit` automatically to **2,463,962,727 bytes** from
host RAM, which is roughly ten times the container limit. Left alone, a single
aggregation could allocate its way into an OOM kill. Pinned to 128 MiB so an
over-large query returns an AQL error instead of the process vanishing.

Its healthcheck took two attempts: `/_api/version` returns 401 without
credentials, and the image ships BusyBox wget, which has no `--user` or
`--password`, so the credentials go in the URL.

**Memgraph** needs `--memory-limit=200`, i.e. below the container limit, so that
it refuses a query with a memory error rather than being OOM-killed by the
kernel. An error naming the query is diagnosable; a disappeared process is not.
Durability is off (`--storage-wal-enabled=false`, no snapshots), which is what
Memgraph documents for query benchmarking. That is a genuine asymmetry against
Neo4j and ArangoDB, which are left durable, and it is repeated next to
Memgraph's numbers in the results rather than buried here.

**FalkorDB** defaults to one reader thread per core and would have started 8 of
them to share half a core, measuring scheduler contention instead of the engine.
Pinned to `THREAD_COUNT 1`. `--maxmemory 200mb` for the same reason as
Memgraph's limit.

## What is not enforced

Disk. `storage_opt.size` needs a quota-capable storage driver and Docker
Desktop's overlay2 on macOS is not one, so the 1 GB figure is declared and not
enforced. It is also not binding: the prepared dataset is 2.7 MB of CSV and no
engine's store comes close to 1 GB. Recorded as declared-not-enforced rather
than claimed as enforced.

CPU is capped by CFS quota (`cpus: 0.5`), which is not the same thing as
CognoDB's burstable 0.5 vCPU. A CFS quota is a hard ceiling every 100 ms
period; a burstable cloud vCPU can exceed its baseline while it has credit and
then get throttled hard when the credit runs out. So the local containers should
show steadier tail latency than a burstable cloud instance, and any comparison
of p95 between the two tracks has to account for that.
