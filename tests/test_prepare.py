import gzip

import pytest

from graphbench.datasets import prepare
from graphbench.datasets.registry import Dataset

FAKE = Dataset(
    name="fake",
    url="file:///dev/null",
    sha256="",
    undirected=True,
    node_label="Author",
    rel_type="COAUTHOR",
    citation="none",
)


def write_edges(tmp_path, text: str, gz: bool = False):
    if gz:
        p = tmp_path / "edges.txt.gz"
        with gzip.open(p, "wt") as fh:
            fh.write(text)
    else:
        p = tmp_path / "edges.txt"
        p.write_text(text)
    return p


def test_comments_and_blank_lines_are_skipped(tmp_path):
    p = write_edges(tmp_path, "# Directed graph\n# Nodes: 3\n1 2\n\n2 3\n")
    assert prepare.read_edges(p, undirected=True) == [(1, 2), (2, 3)]


def test_reverse_duplicates_collapse_when_undirected(tmp_path):
    p = write_edges(tmp_path, "1 2\n2 1\n")
    assert prepare.read_edges(p, undirected=True) == [(1, 2)]


def test_reverse_duplicates_survive_when_directed(tmp_path):
    p = write_edges(tmp_path, "1 2\n2 1\n")
    assert prepare.read_edges(p, undirected=False) == [(1, 2), (2, 1)]


def test_self_loops_are_dropped(tmp_path):
    p = write_edges(tmp_path, "1 1\n1 2\n")
    assert prepare.read_edges(p, undirected=True) == [(1, 2)]


def test_gzip_and_plain_parse_the_same(tmp_path):
    body = "1 2\n2 3\n"
    plain = prepare.read_edges(write_edges(tmp_path, body), True)
    gz = prepare.read_edges(write_edges(tmp_path, body, gz=True), True)
    assert plain == gz


def test_cohort_is_stable_and_in_range():
    # Stability across processes is the whole reason this uses crc32, so pin one
    # known value rather than only checking the range.
    assert prepare.cohort_of(3) == prepare.cohort_of(3)
    assert all(0 <= prepare.cohort_of(i) < prepare.N_COHORTS for i in range(500))
    assert prepare.cohort_of(3) == 27


def test_cohorts_are_reasonably_spread():
    counts = [0] * prepare.N_COHORTS
    for i in range(10_000):
        counts[prepare.cohort_of(i)] += 1
    # crc32 should not leave a bucket empty over 10k ids
    assert min(counts) > 0
    assert max(counts) < 3 * (10_000 / prepare.N_COHORTS)


def test_start_nodes_respect_the_degree_band():
    lo, hi = prepare.DEGREE_BAND
    degrees = {i: i % 200 for i in range(5_000)}
    picked = prepare.pick_start_nodes(degrees, seed=1)
    assert len(picked) == prepare.START_NODE_COUNT
    assert all(lo <= degrees[n] <= hi for n in picked)
    assert picked == sorted(picked)


def test_start_nodes_are_seed_stable():
    degrees = {i: i % 200 for i in range(5_000)}
    assert prepare.pick_start_nodes(degrees, 42) == prepare.pick_start_nodes(degrees, 42)


def test_start_nodes_fall_back_when_nothing_is_in_band():
    # Every node is a hub. Rather than return nothing and abort a benchmark run,
    # take what is there and let the manifest record the degree stats.
    degrees = {1: 900, 2: 950}
    assert prepare.pick_start_nodes(degrees, seed=1) == [1, 2]


@pytest.mark.parametrize("node_id", [0, 7, 12345])
def test_keys_are_prefixed_strings(node_id):
    assert prepare.key_for(FAKE, node_id) == f"a{node_id}"
