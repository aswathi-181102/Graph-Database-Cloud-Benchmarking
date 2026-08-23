"""Read side of the prepared dataset. Adapters use this, nothing else.

Two deliberate choices here.

Batches are yielded, not returned as one list. Not for ca-AstroPh's sake, where
198k rows would fit in memory fine, but because the ingest metric is
nodes/second and relationships/second: the loader has to be able to hand an
engine one batch at a time so the timer measures the database accepting writes
rather than Python building a list first.

Types are cast here, once, rather than in each adapter. If one adapter passed
`degree` as a string and another as an int, the filtered-lookup query would be
doing string comparison on one platform and integer comparison on another, and
the resulting latency gap would look like an engine difference.
"""

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from graphbench import paths


@dataclass(frozen=True)
class PreparedGraph:
    directory: Path
    manifest: dict
    start_keys: list[str]

    @property
    def node_count(self) -> int:
        return int(self.manifest["nodes"])

    @property
    def edge_count(self) -> int:
        return int(self.manifest["edges"])

    @property
    def node_label(self) -> str:
        return self.manifest["node_label"]

    @property
    def rel_type(self) -> str:
        return self.manifest["rel_type"]

    @property
    def cohorts(self) -> int:
        return int(self.manifest["cohorts"])

    def iter_nodes(self, batch_size: int) -> Iterator[list[dict]]:
        with (self.directory / "nodes.csv").open(newline="") as fh:
            batch: list[dict] = []
            for row in csv.DictReader(fh):
                batch.append(
                    {
                        "id": int(row["id"]),
                        "key": row["key"],
                        "cohort": int(row["cohort"]),
                        "degree": int(row["degree"]),
                    }
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

    def iter_edges(self, batch_size: int) -> Iterator[list[dict]]:
        with (self.directory / "edges.csv").open(newline="") as fh:
            batch: list[dict] = []
            for row in csv.DictReader(fh):
                batch.append({"src": int(row["src"]), "dst": int(row["dst"])})
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch


def load(dataset: str) -> PreparedGraph:
    directory = paths.PREPARED_DIR / dataset
    manifest_file = directory / "manifest.json"
    if not manifest_file.exists():
        # Explicit instruction instead of a bare FileNotFoundError, because this
        # is the single most likely first-run mistake for anyone reproducing the
        # benchmark from the README.
        raise FileNotFoundError(
            f"{dataset} is not prepared yet, run: graphbench dataset prepare --dataset {dataset}"
        )
    manifest = json.loads(manifest_file.read_text())
    starts = json.loads((directory / "start_nodes.json").read_text())
    return PreparedGraph(directory, manifest, starts["keys"])
