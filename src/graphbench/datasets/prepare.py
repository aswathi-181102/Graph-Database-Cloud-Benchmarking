"""Turn a raw SNAP edge list into the canonical CSV every adapter loads."""

import gzip
import json
import random
import zlib
from datetime import UTC, datetime
from pathlib import Path

from graphbench import paths
from graphbench.datasets import fetch, sample
from graphbench.datasets.registry import Dataset

# Source graphs have no attributes, so key/cohort/degree are derived from the node
# id. See docs/DECISIONS.md section 3.
N_COHORTS = 32

DEGREE_BAND = (5, 50)
START_NODE_COUNT = 256

DEFAULT_SEED = 7919


def _open_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return path.open("rt")


def key_for(ds: Dataset, node_id: int) -> str:
    # String, not the raw int: integer keys let some engines fall back to internal
    # id addressing, which is fast and unrepresentative.
    return f"{ds.node_label[0].lower()}{node_id}"


def read_edges(path: Path, undirected: bool) -> list[tuple[int, int]]:
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
            # reverse edges.
            seen.add((min(u, v), max(u, v)) if undirected else (u, v))
    return sorted(seen)


def cohort_of(node_id: int) -> int:
    # crc32, not hash(): hash() on str is salted per process.
    return zlib.crc32(str(node_id).encode()) % N_COHORTS


def pick_start_nodes(degrees: dict[int, int], seed: int) -> list[int]:
    lo, hi = DEGREE_BAND
    eligible = sorted(n for n, d in degrees.items() if lo <= d <= hi)
    if not eligible:
        # Odd graph shape. Better to run on a bad start set and say so in the
        # manifest than to abort the benchmark.
        eligible = sorted(degrees)
    rng = random.Random(seed)
    if len(eligible) <= START_NODE_COUNT:
        return eligible
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
        # Recompute against the subgraph, or the start-node band picks nodes whose
        # neighbours mostly got sampled away.
        adj = sample.build_adjacency(edges)
        sampled = True
        print(f"  {len(adj):,} nodes / {len(edges):,} edges after sampling")

    # Undirected degree even for a directed source, because the traversal workload
    # uses an undirected pattern.
    degrees = {n: len(neigh) for n, neigh in adj.items()}
    nodes = sorted(adj)

    # Everything below is written sorted. Insert order changes which pages are
    # hot, so shuffled input on one engine would not be a fair comparison.
    with (out_dir / "nodes.csv").open("w") as fh:
        fh.write("id,key,cohort,degree\n")
        for n in nodes:
            fh.write(f"{n},{key_for(ds, n)},{cohort_of(n)},{degrees[n]}\n")

    with (out_dir / "edges.csv").open("w") as fh:
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

    # Provenance record. Anything that could make two runs incomparable goes here.
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
        "degree_band": list(DEGREE_BAND),
        "prepared_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"wrote {paths.display(out_dir)}/")
    return out_dir
