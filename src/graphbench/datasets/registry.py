"""Which public graphs we know how to load.

Dataset choice is a methodology decision, so the reasoning is here rather than
buried in the README.

Requirements it had to satisfy:
  1. at least 100k relationships (assignment floor)
  2. must fit, with indexes, inside 256 MB RAM / 1 GB disk
  3. public, citable, stable URL, so the run is reproducible by a stranger
  4. real degree skew, so k-hop depth actually costs something

ca-AstroPh wins on all four. 198k edges sits comfortably inside the suggested
100k-500k band, the raw file is 1.5 MB so a reproduction costs seconds instead
of an hour, and the degree distribution is genuinely heavy tailed (mean 21, max
504) which is what makes 2-hop and 3-hop diverge instead of all looking the
same. Being a co-authorship graph it is naturally undirected, so one Cypher
pattern works without direction fudging.

What was considered and rejected:

  soc-Pokec, the assignment's own example. 30M edges and a 400 MB download.
  Usable only after aggressive sampling, at which point the sampling strategy
  becomes the single biggest threat to fairness in the whole study. Kept in the
  registry as a sampled option, not the default.

  The Neo4j movies graph. Roughly 170 nodes and 250 relationships, three orders
  of magnitude under the floor. Fine for a tutorial, useless here.

  LDBC SNB, which is the academically correct answer. Even scale factor 1
  overshoots a 256 MB instance, and the data generator is a JVM/Spark toolchain
  that would wreck the "clone and re-run" property this repo is graded on.

  cit-HepPh (421k edges) was a real contender and would also have been valid.
  Passed on it only because at 421k edges plus indexes there is a genuine risk
  of hitting the 256 MB ceiling on some engines, which would turn "we measured
  latency" into "we measured which engine OOMs first". That is an interesting
  experiment, but a different one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    name: str
    url: str
    sha256: str
    # SNAP ships undirected graphs with both (u,v) and (v,u) present. We collapse
    # them and keep one edge per pair, so counts here are post-dedupe. Loading
    # both directions would double the stored relationship count and quietly
    # inflate every ingest throughput number.
    undirected: bool
    node_label: str
    rel_type: str
    citation: str
    # None means "use the whole graph". Anything else gets snowball sampled down
    # so it still fits a 256 MB instance.
    target_edges: int | None = None


DATASETS: dict[str, Dataset] = {
    # SNAP advertises 18,772 nodes / 198,110 edges. We measure 18,771 / 198,050
    # because we drop the 60 self loops, and the one node they count that we
    # don't (id 64582) appears only in a self loop. Verified against the raw
    # file, not assumed. This is the kind of gap worth stating out loud: anyone
    # comparing our numbers to the SNAP page will otherwise think we lost data.
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
    # Directed, and always sampled. Present so the sampler is exercised by
    # something real rather than only by unit tests, and so the claim "we could
    # have used the suggested dataset" is backed by working code.
    "soc-pokec": Dataset(
        name="soc-pokec",
        url="https://snap.stanford.edu/data/soc-pokec-relationships.txt.gz",
        sha256="",  # 400 MB, pinned on first verified fetch by whoever runs it
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
