"""Public graphs we know how to load. Selection reasoning: docs/DECISIONS.md#3."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    name: str
    url: str
    sha256: str
    # SNAP lists undirected edges twice. We keep one per pair, so counts here are
    # post-dedupe.
    undirected: bool
    node_label: str
    rel_type: str
    citation: str
    # None means use the whole graph, otherwise snowball sample down to this.
    target_edges: int | None = None


DATASETS: dict[str, Dataset] = {
    # SNAP advertises 18,772 / 198,110. We measure 18,771 / 198,050: 60 self loops
    # dropped, and node 64582 only ever appears in one of them. Verified, not
    # assumed.
    "ca-astroph": Dataset(
        name="ca-astroph",
        url="https://snap.stanford.edu/data/ca-AstroPh.txt.gz",
        sha256="51bf1e2cace269b884481a8502474efa67c0fd01d998ff7f5a154d7d3e527f27",
        undirected=True,
        node_label="Author",
        rel_type="COAUTHOR",
        citation=(
            "J. Leskovec, J. Kleinberg, C. Faloutsos. Graph Evolution: Densification "
            "and Shrinking Diameters. ACM TKDD, 2007. SNAP ca-AstroPh."
        ),
    ),
    # Directed and always sampled. Here so the sampler is exercised by real data,
    # not just unit tests.
    "soc-pokec": Dataset(
        name="soc-pokec",
        url="https://snap.stanford.edu/data/soc-pokec-relationships.txt.gz",
        sha256="",  # 400 MB, pinned on first verified fetch
        undirected=False,
        node_label="Person",
        rel_type="FOLLOWS",
        citation=(
            "L. Takac, M. Zabovsky. Data Analysis in Public Social Networks. "
            "Intl. Scientific Conf. & Workshop Present Day Trends of Innovations, 2012. "
            "SNAP soc-Pokec."
        ),
        target_edges=300_000,
    ),
}

DEFAULT_DATASET = "ca-astroph"


def get(name: str) -> Dataset:
    try:
        return DATASETS[name]
    except KeyError:
        known = ", ".join(sorted(DATASETS))
        raise KeyError(f"unknown dataset {name!r}, known: {known}") from None
