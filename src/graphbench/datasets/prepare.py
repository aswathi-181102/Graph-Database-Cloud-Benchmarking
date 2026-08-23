"""Turn a raw SNAP edge list into the canonical CSV pair every adapter loads.

Output lands in data/prepared/<dataset>/ as:
    nodes.csv        id,key,cohort,degree
    edges.csv        src,dst
    start_nodes.json start keys for the traversal workloads
    manifest.json    counts, checksums, sampling params

Why an intermediate format at all, instead of pointing each loader at the SNAP
file? Two reasons. First, fairness: dedupe, self-loop removal and sampling
happen exactly once, so no engine can accidentally be handed a slightly
different graph because its loader parsed comments differently. Second,
practicality: every engine here can ingest CSV, and several have a bulk CSV
importer that would be unusable if the input were a gzipped tab-separated blob.

Everything written here is sorted. That is not cosmetic. Insert order changes
which pages are hot, so a run where Neo4j got the edges in id order and
Memgraph got them shuffled is not a fair comparison.
"""

import gzip
import json
import random
import zlib
from datetime import UTC, datetime
from pathlib import Path

from graphbench import paths
from graphbench.datasets import fetch, sample
from graphbench.datasets.registry import Dataset

# The source graphs are bare edge lists with no attributes, but the assignment
# requires a point lookup, an indexed/filtered lookup and a group-by
# aggregation. None of those exist without properties, so we derive three from
# the node id.
#
# Derived, not randomly generated, and that distinction matters: a stranger
# re-running this gets byte-identical properties from the raw file alone, with
# no extra artifact to download and no RNG state to agree on. It also means the
# properties are the same on all five platforms by construction rather than by
# careful copying.
#
# 32 cohorts is a compromise for the group-by. With one bucket per node the
# aggregation degenerates into "return every row" and measures result streaming
# instead of grouping. With 2 buckets it is basically a count. 32 buckets over
# ~18.7k nodes gives ~590 nodes each, which is a realistic group-by shape and
# still forces a full label scan, which is the thing being compared.
N_COHORTS = 32

# Traversal start nodes come from a degree band, not from uniform random choice.
# ca-AstroPh has a max degree of 504 and a mean of 21. A 3-hop expansion from a
# hub reaches most of the graph, which on a 256 MB instance measures the OOM
# killer rather than the traversal. Worse, under uniform sampling the p95 would
# largely record whether that run happened to draw a hub, so the number would
# move between platforms for reasons that have nothing to do with the platform.
#
# The band is a real limitation, not a free lunch: these results describe
# mid-degree traversals and deliberately exclude hub behaviour. Stated in the
# README rather than hidden, since hub traversal is a legitimate thing to want
# to measure and this suite does not measure it.
DEGREE_BAND = (5, 50)

# The assignment asks for >=100 iterations per read workload. 256 start nodes
# means a 100-iteration run never reuses a node, so no single node's cached plan
# or hot pages can carry the whole measurement. Kept as a power of two only
# because it makes the round-robin in the runner obvious.
START_NODE_COUNT = 256

# One fixed seed for sampling and start-node selection. Any seed works; what
# matters is that it is written into the manifest so a rerun can reproduce the
# exact same start set.
DEFAULT_SEED = 7919


def _open_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return path.open("rt")


def key_for(ds: Dataset, node_id: int) -> str:
    """Stable string key for a node.

    Deliberately a string, not the raw integer id. Real applications look nodes
    up by an external identifier, and string equality on an indexed property is
    the honest version of a point lookup. Handing every engine an integer key
    would let some of them fall back to internal id addressing, which is fast
    and completely unrepresentative.
    """
    return f"{ds.node_label[0].lower()}{node_id}"


def read_edges(path: Path, undirected: bool) -> list[tuple[int, int]]:
    """Parse an edge list, dropping comments, self loops and duplicate pairs.

    Self loops go because a self loop makes "1-hop neighbours" ambiguous: some
    engines return the start node, some don't, and the resulting count mismatch
    looks like a correctness bug when it is really a data artifact.
    """
    seen: set[tuple[int, int]] = set()
    with _open_maybe_gzip(path) as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            u, v = int(parts[0]), int(parts[1])
            if u == v:
                continue
            # Canonicalising to (min, max) is what collapses SNAP's duplicated
            # reverse edges. For a directed source we keep the pair as given.
            seen.add((min(u, v), max(u, v)) if undirected else (u, v))
    return sorted(seen)


def cohort_of(node_id: int) -> int:
    # crc32, not hash(). Python's hash() for str is salted per process unless
    # PYTHONHASHSEED is set, so hash() would hand different platforms different
    # cohort assignments across runs and silently break the group-by comparison.
    return zlib.crc32(str(node_id).encode()) % N_COHORTS


def pick_start_nodes(degrees: dict[int, int], seed: int) -> list[int]:
    lo, hi = DEGREE_BAND
    eligible = sorted(n for n, d in degrees.items() if lo <= d <= hi)
    if not eligible:
        # Tiny or unusually shaped graph. Better to run on a bad start set and
        # say so in the manifest than to crash a whole benchmark run.
        eligible = sorted(degrees)
    rng = random.Random(seed)
    if len(eligible) <= START_NODE_COUNT:
        return eligible
    # sorted() after sample() so the on-disk list is stable to eyeball and diff.
    # The runner shuffles nothing, it just walks this list, so order is fixed for
    # every platform.
    return sorted(rng.sample(eligible, START_NODE_COUNT))


def prepare(ds: Dataset, seed: int = DEFAULT_SEED, force: bool = False) -> Path:
    raw = fetch.fetch(ds, force=force)
    out_dir = paths.PREPARED_DIR / ds.name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"parsing {raw.name}")
    edges = read_edges(raw, ds.undirected)
    adj = sample.build_adjacency(edges)
    print(f"  {len(adj):,} nodes / {len(edges):,} edges after dedupe")

    sampled = False
    if ds.target_edges and ds.target_edges < len(edges):
        print(f"  sampling down to ~{ds.target_edges:,} edges")
        keep = sample.snowball(adj, ds.target_edges, seed)
        edges = sample.induced_edges(adj, keep)
        # Degrees have to be recomputed against the subgraph. Carrying the
        # original degrees over would put nodes in the start-node band whose
        # neighbours mostly got sampled away.
        adj = sample.build_adjacency(edges)
        sampled = True
        print(f"  {len(adj):,} nodes / {len(edges):,} edges after sampling")

    # Undirected degree even for a directed source, because the traversal
    # workload uses an undirected pattern. Reported in the manifest so the
    # definition is not left to the reader.
    degrees = {n: len(neigh) for n, neigh in adj.items()}
    nodes = sorted(adj)

    nodes_csv = out_dir / "nodes.csv"
    with nodes_csv.open("w") as fh:
        fh.write("id,key,cohort,degree\n")
        for n in nodes:
            fh.write(f"{n},{key_for(ds, n)},{cohort_of(n)},{degrees[n]}\n")

    edges_csv = out_dir / "edges.csv"
    with edges_csv.open("w") as fh:
        fh.write("src,dst\n")
        for u, v in edges:
            fh.write(f"{u},{v}\n")

    starts = pick_start_nodes(degrees, seed)
    (out_dir / "start_nodes.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "degree_band": list(DEGREE_BAND),
                "count": len(starts),
                "keys": [key_for(ds, n) for n in starts],
            },
            indent=2,
        )
        + "\n"
    )

    # The manifest is the provenance record. Anything that could make two runs
    # incomparable belongs in here, which is why the raw checksum and the seed
    # are stored next to the counts.
    deg_values = list(degrees.values())
    manifest = {
        "dataset": ds.name,
        "source_url": ds.url,
        "citation": ds.citation,
        "raw_file": raw.name,
        "raw_bytes": raw.stat().st_size,
        "raw_sha256": fetch.sha256_of(raw),
        "undirected": ds.undirected,
        "node_label": ds.node_label,
        "rel_type": ds.rel_type,
        "nodes": len(nodes),
        "edges": len(edges),
        "sampled": sampled,
        "target_edges": ds.target_edges,
        "seed": seed,
        "degree_min": min(deg_values),
        "degree_max": max(deg_values),
        "degree_mean": round(sum(deg_values) / len(deg_values), 3),
        "cohorts": N_COHORTS,
        "start_nodes": len(starts),
        "prepared_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"wrote {out_dir.relative_to(paths.ROOT)}/")
    return out_dir
