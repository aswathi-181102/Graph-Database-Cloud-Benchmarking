"""Read side of the prepared dataset. Adapters use this, nothing else.

Batches are yielded rather than returned whole so the ingest timer measures the
engine accepting writes, not Python building a list. Types are cast here once, so
no adapter can end up comparing a string `degree` against an int one.
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
        # Most likely first-run mistake, so say what to do about it.
        raise FileNotFoundError(
            f"{dataset} is not prepared yet, run: graphbench dataset prepare --dataset {dataset}"
        )
    manifest = json.loads(manifest_file.read_text())
    starts = json.loads((directory / "start_nodes.json").read_text())
    return PreparedGraph(directory, manifest, starts["keys"])
