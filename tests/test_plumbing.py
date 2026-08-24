"""Tests for the supporting pieces: doctor, fetch verification, prepare end to end,
container control and environment capture.

The parts that genuinely need Docker or the network are exercised by monkeypatching
the one function that shells out, so the parsing and control flow around it are
covered without the test suite depending on a daemon.
"""

import gzip

import pytest

from graphbench import dockerctl, doctor, environment
from graphbench.config import Platform, Tier
from graphbench.datasets import fetch, prepare
from graphbench.datasets.registry import Dataset


def plat(pid, track="local", missing=(), engine="neo4j"):
    return Platform(
        id=pid,
        display=pid,
        engine=engine,
        adapter="bolt",
        track=track,
        tier=Tier(name="t", vcpu=0.5, ram_mb=256, disk_gb=1),
        missing_env=tuple(missing),
    )


# --------------------------------------------------------------------- doctor --


def test_doctor_marks_ready_and_skipped():
    out = doctor.render([plat("ready-one"), plat("no-creds", missing=["A_URI", "A_PASS"])])
    assert "ready" in out
    assert "skip: missing A_URI, A_PASS" in out


def test_doctor_counts_by_track():
    out = doctor.render([plat("a"), plat("b"), plat("c", track="cloud")])
    assert "3 of 3 platforms ready" in out
    assert "'local': 2" in out


def test_doctor_warns_when_the_local_track_is_too_thin_to_compare():
    # One local platform cannot demonstrate resource parity against anything.
    out = doctor.render([plat("only-one")])
    assert "no resource-matched comparison" in out


def test_doctor_does_not_warn_with_enough_local_platforms():
    out = doctor.render([plat("a"), plat("b")])
    assert "no resource-matched comparison" not in out


def test_doctor_warns_when_the_subject_of_the_study_is_missing():
    out = doctor.render([plat("a"), plat("b")])
    assert "CognoDB is not configured" in out


def test_doctor_is_quiet_when_cognodb_is_present():
    out = doctor.render([plat("a"), plat("b"), plat("cognodb", engine="cognodb")])
    assert "CognoDB is not configured" not in out


def test_doctor_aligns_into_columns():
    out = doctor.render([plat("short"), plat("a-much-longer-platform-id")])
    header, rule, *rows = out.splitlines()
    assert set(rule) <= {"-", " "}
    assert len(rule) == len(header)


# ---------------------------------------------------------------------- fetch --


def test_sha256_matches_a_known_value(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"abc")
    assert fetch.sha256_of(f) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_reads_in_chunks_without_loading_the_file(tmp_path):
    # bigger than CHUNK, so the streaming path is the one under test
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * (fetch.CHUNK + 5))
    assert len(fetch.sha256_of(f)) == 64


def test_cached_file_with_a_matching_checksum_is_accepted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch.paths, "RAW_DIR", tmp_path)
    body = b"1 2\n2 3\n"
    (tmp_path / "edges.txt").write_bytes(body)
    digest = fetch.sha256_of(tmp_path / "edges.txt")

    ds = Dataset(
        name="d", url="https://example.invalid/edges.txt", sha256=digest,
        undirected=True, node_label="Author", rel_type="COAUTHOR", citation="none",
    )
    assert fetch.fetch(ds) == tmp_path / "edges.txt"
    assert "already have" in capsys.readouterr().out


def test_a_checksum_mismatch_is_a_hard_failure(tmp_path, monkeypatch):
    """A benchmark that ran on the wrong input is worse than one that did not run."""
    monkeypatch.setattr(fetch.paths, "RAW_DIR", tmp_path)
    (tmp_path / "edges.txt").write_bytes(b"tampered")
    ds = Dataset(
        name="d", url="https://example.invalid/edges.txt", sha256="0" * 64,
        undirected=True, node_label="Author", rel_type="COAUTHOR", citation="none",
    )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        fetch.fetch(ds)


def test_an_unpinned_checksum_is_reported_rather_than_trusted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch.paths, "RAW_DIR", tmp_path)
    (tmp_path / "edges.txt").write_bytes(b"1 2\n")
    ds = Dataset(
        name="d", url="https://example.invalid/edges.txt", sha256="",
        undirected=True, node_label="Author", rel_type="COAUTHOR", citation="none",
    )
    fetch.fetch(ds)
    assert "sha256 not pinned yet" in capsys.readouterr().out


# -------------------------------------------------------------------- prepare --


def test_prepare_writes_a_complete_dataset(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "graph.txt.gz"
    with gzip.open(src, "wt") as fh:
        fh.write("# comment\n")
        for i in range(60):
            fh.write(f"{i} {i + 1}\n")
            fh.write(f"{i + 1} {i}\n")  # duplicate reverse edge
        fh.write("5 5\n")  # self loop

    monkeypatch.setattr(prepare.paths, "RAW_DIR", raw)
    monkeypatch.setattr(prepare.paths, "PREPARED_DIR", tmp_path / "prepared")
    monkeypatch.setattr(prepare.paths, "ROOT", tmp_path)

    ds = Dataset(
        name="tiny", url="https://example.invalid/graph.txt.gz", sha256="",
        undirected=True, node_label="Author", rel_type="COAUTHOR", citation="cite me",
    )
    out = prepare.prepare(ds, seed=1)

    import json

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["edges"] == 60  # reverse duplicates collapsed, self loop dropped
    assert manifest["nodes"] == 61
    assert manifest["citation"] == "cite me"
    assert manifest["seed"] == 1
    assert manifest["degree_band"] == list(prepare.DEGREE_BAND)
    assert len(manifest["raw_sha256"]) == 64

    nodes = (out / "nodes.csv").read_text().splitlines()
    assert nodes[0] == "id,key,cohort,degree"
    assert len(nodes) == 62  # header + 61

    starts = json.loads((out / "start_nodes.json").read_text())
    assert starts["keys"] and all(k.startswith("a") for k in starts["keys"])


def test_prepare_samples_when_the_graph_exceeds_the_target(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    with (raw / "graph.txt").open("w") as fh:
        for i in range(200):
            fh.write(f"{i} {i + 1}\n")

    monkeypatch.setattr(prepare.paths, "RAW_DIR", raw)
    monkeypatch.setattr(prepare.paths, "PREPARED_DIR", tmp_path / "prepared")
    monkeypatch.setattr(prepare.paths, "ROOT", tmp_path)

    ds = Dataset(
        name="tiny", url="https://example.invalid/graph.txt", sha256="",
        undirected=True, node_label="Person", rel_type="FOLLOWS", citation="c",
        target_edges=50,
    )
    out = prepare.prepare(ds, seed=1)

    import json

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["sampled"] is True
    assert manifest["target_edges"] == 50
    assert 50 <= manifest["edges"] < 200


# ------------------------------------------------------------------ dockerctl --


def test_wait_healthy_returns_true_once_the_probe_passes(monkeypatch):
    states = iter(["starting", "unhealthy", "healthy"])
    monkeypatch.setattr(dockerctl, "_inspect", lambda c, t: next(states))
    assert dockerctl.wait_healthy("c", timeout=5, poll=0.01) is True


def test_wait_healthy_accepts_a_running_container_with_no_healthcheck(monkeypatch):
    def inspect(container, template):
        return "" if "Health" in template else "true"

    monkeypatch.setattr(dockerctl, "_inspect", inspect)
    assert dockerctl.wait_healthy("c", timeout=5, poll=0.01) is True


def test_wait_healthy_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(dockerctl, "_inspect", lambda c, t: "unhealthy")
    assert dockerctl.wait_healthy("c", timeout=0.05, poll=0.01) is False


def test_volumes_of_parses_the_inspect_output(monkeypatch):
    monkeypatch.setattr(dockerctl, "_inspect", lambda c, t: "vol-a vol-b ")
    assert dockerctl.volumes_of("c") == ["vol-a", "vol-b"]


def test_volumes_of_is_empty_when_inspect_fails(monkeypatch):
    monkeypatch.setattr(dockerctl, "_inspect", lambda c, t: None)
    assert dockerctl.volumes_of("c") == []


def test_recreate_refuses_without_a_daemon(monkeypatch):
    monkeypatch.setattr(dockerctl, "available", lambda: False)
    with pytest.raises(dockerctl.DockerUnavailable, match="not reachable"):
        dockerctl.recreate("svc", "container")


# ---------------------------------------------------------------- environment --


def test_describe_records_what_a_rerun_would_need():
    env = environment.describe()
    assert env["python"]
    assert env["client_os"]
    assert "neo4j" in env["packages"]
    assert isinstance(env["git_dirty"], bool)


def test_missing_package_is_named_rather_than_omitted(monkeypatch):
    monkeypatch.setattr(environment, "TRACKED_PACKAGES", ("definitely-not-installed",))
    assert environment.describe()["packages"]["definitely-not-installed"] == "not installed"


def test_container_stats_parses_docker_output(monkeypatch):
    calls = []

    def fake_cmd(args):
        calls.append(args)
        if "stats" in args:
            return "148.1MiB / 256MiB|57.86%|1.22%"
        return "226451"

    monkeypatch.setattr(environment, "_cmd", fake_cmd)
    stats = environment.container_stats("gb-memgraph", "/var/lib/memgraph")
    assert stats["observable"] is True
    assert stats["mem_usage"] == "148.1MiB / 256MiB"
    assert stats["mem_percent"] == "57.86%"
    assert stats["data_dir_bytes"] == 226451
    # -L so a symlinked data dir is followed, which is how falkordb reported 22 bytes
    assert any("-sbL" in " ".join(c) for c in calls)


def test_container_stats_falls_back_to_kilobytes(monkeypatch):
    def fake_cmd(args):
        joined = " ".join(args)
        if "stats" in joined:
            return "10MiB / 256MiB|4%|0%"
        if "-sbL" in joined:
            return None  # BusyBox du has no -b
        return "12"

    monkeypatch.setattr(environment, "_cmd", fake_cmd)
    stats = environment.container_stats("c", "/data")
    assert stats["data_dir_bytes"] == 12 * 1024


def test_container_stats_says_not_observable_when_docker_is_silent(monkeypatch):
    monkeypatch.setattr(environment, "_cmd", lambda args: None)
    stats = environment.container_stats("gone")
    assert stats["observable"] is False
    assert "gone" in stats["reason"]


def test_container_stats_rejects_unexpected_output(monkeypatch):
    monkeypatch.setattr(environment, "_cmd", lambda args: "not-pipe-separated")
    assert environment.container_stats("c")["observable"] is False
