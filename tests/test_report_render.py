import json

import pytest

from graphbench.report import render, variance


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """A minimal but structurally real results directory.

    ROOT is redirected as well as RESULTS_DIR, because render.build() injects into
    ROOT/README.md. Without that this fixture rewrote the repo's own README with
    fixture data, which is how "Engine One" ended up in it.
    """
    monkeypatch.setattr(render.paths, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(variance.paths, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(render.paths, "ROOT", tmp_path)

    def write(run_id, load_seconds, hop3):
        d = tmp_path / run_id
        d.mkdir()
        (d / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": "2026-08-24T00:00:00+00:00",
                    "finished_at": "2026-08-24T00:10:00+00:00",
                    "environment": {
                        "client_os": "Darwin 25.5.0",
                        "client_machine": "arm64",
                        "client_cpu_count": 8,
                        "client_memory_bytes": 8 * 10**9,
                        "python": "3.13.15",
                        "packages": {"neo4j": "5.28.4"},
                        "git_commit": "abc123def456789",
                        "git_dirty": False,
                    },
                    "dataset": {
                        "dataset": "tiny",
                        "nodes": 5,
                        "edges": 4,
                        "raw_sha256": "0" * 64,
                        "source_url": "file:///tiny",
                        "degree_min": 1,
                        "degree_mean": 1.6,
                        "degree_max": 2,
                        "start_nodes": 5,
                        "seed": 1,
                        "degree_band": [1, 2],
                    },
                    "workloads": {
                        "batch_size": 1000,
                        "iterations": 100,
                        "warmup": 20,
                        "hops": [1, 3],
                        "timeout_s": 30,
                        "concurrency": [1],
                        "duration_s": 30,
                        "read_ratio": 0.9,
                        "mixed_skipped": False,
                    },
                    "skipped": [{"id": "cognodb-cloud", "reason": "missing COGNODB_URI"}],
                    "verification": {"compared": 12, "agree": True, "clean": True,
                                     "mismatches": []},
                    "platforms": [{"id": "e1", "status": "ok"}],
                },
                indent=2,
            )
        )
        (d / "e1.json").write_text(
            json.dumps(
                {
                    "platform": {
                        "id": "e1",
                        "display": "Engine One",
                        "engine": "e1",
                        "track": "local",
                        "tier": {"name": "docker", "vcpu": 0.5, "ram_mb": 256,
                                 "disk_gb": 1, "burstable": False, "notes": "",
                                 "source": "docker-compose.yml"},
                    },
                    "dataset": {"nodes": 5, "edges": 4},
                    "server_version": "E1 1.0",
                    "status": "ok",
                    "errors": [],
                    "duration_s": 12.0,
                    "reset": {"reset": True, "volumes_removed": ["v"]},
                    "load": {
                        "method": "driver batching",
                        "nodes_per_second": 100.0,
                        "rels_per_second": 200.0,
                        "total_seconds": load_seconds,
                        "phases": [
                            {"name": "nodes", "rows": 5, "seconds": 1.0},
                            {"name": "indexes", "rows": 0, "seconds": 0.1},
                            {"name": "edges", "rows": 4, "seconds": 1.0},
                        ],
                    },
                    "indexes": ["index on key"],
                    "reads": [
                        {"name": "baseline_noop", "abandoned": False,
                         "latency": {"p50_ms": 0.3, "p95_ms": 0.5, "errors": 0}},
                        {"name": "point_lookup", "abandoned": False,
                         "latency": {"p50_ms": 0.4, "p95_ms": 0.6, "errors": 0}},
                        {"name": "traversal_1hop", "abandoned": False,
                         "latency": {"p50_ms": 0.5, "p95_ms": 0.9, "errors": 0}},
                        {"name": "traversal_3hop", "abandoned": False,
                         "latency": {"p50_ms": hop3, "p95_ms": hop3 * 4, "errors": 0}},
                        {"name": "filtered_lookup", "abandoned": False,
                         "latency": {"p50_ms": 0.6, "p95_ms": 0.8, "errors": 0}},
                        {"name": "aggregation_groupby_cohort", "abandoned": False,
                         "expected": 5, "matches_expected": True,
                         "latency": {"p50_ms": 1.0, "p95_ms": 2.0, "errors": 0}},
                    ],
                    "mixed": {"read_ratio": 0.9, "writes_cleaned": 3, "errors": [],
                              "levels": [{"concurrency": 1, "qps": 100.0, "reads": 90,
                                          "writes": 10,
                                          "read_latency": {"p95_ms": 1.0, "errors": 0},
                                          "write_latency": {"p95_ms": 2.0, "errors": 0}}]},
                    "footprint": {"observable": True, "source": "fake"},
                    "container": {"observable": True, "source": "docker stats",
                                  "mem_usage": "100MiB / 256MiB", "mem_percent": "39%",
                                  "data_dir_bytes": 1_000_000},
                },
                indent=2,
            )
        )
        return run_id

    return write


def test_report_survives_a_missing_readme(run_dir, tmp_path, monkeypatch):
    """A renamed or absent README should not cost you the report already written."""
    monkeypatch.setattr(render.paths, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render.paths, "CHARTS_DIR", tmp_path / "docs" / "charts")
    (tmp_path / "docs").mkdir()
    run_dir("r1", 10.0, 5.0)

    assert render.build("r1") == 0
    assert (tmp_path / "docs" / "RESULTS.md").exists()


def test_report_renders_and_names_its_source_run(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render.paths, "CHARTS_DIR", tmp_path / "docs" / "charts")
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        f"# T\n\n{render.BEGIN_MARKER}\nstale\n{render.END_MARKER}\n"
    )
    run_dir("r1", 10.0, 5.0)

    assert render.build("r1") == 0
    assert "Engine One" in (tmp_path / "README.md").read_text()
    text = (tmp_path / "docs" / "RESULTS.md").read_text()
    assert "r1" in text
    assert "Engine One" in text
    assert "Do not edit by hand" in text


def test_report_states_the_verification_outcome(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render.paths, "CHARTS_DIR", tmp_path / "docs" / "charts")
    (tmp_path / "docs").mkdir()
    run_dir("r1", 10.0, 5.0)
    (tmp_path / "README.md").write_text("no markers")
    render.build("r1")
    text = (tmp_path / "docs" / "RESULTS.md").read_text()
    assert "identical values on **12**" in text


def test_report_lists_skipped_platforms_as_skipped(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(render.paths, "CHARTS_DIR", tmp_path / "docs" / "charts")
    (tmp_path / "docs").mkdir()
    run_dir("r1", 10.0, 5.0)
    (tmp_path / "README.md").write_text("no markers")
    render.build("r1")
    text = (tmp_path / "docs" / "RESULTS.md").read_text()
    assert "cognodb-cloud" in text
    assert "not that it failed" in text


def test_missing_run_is_an_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "RESULTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="no completed run"):
        render.load_run()


def test_readme_injection_replaces_only_the_marked_block(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "ROOT", tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Title\n\nhand written above\n\n"
        f"{render.BEGIN_MARKER}\nstale\n{render.END_MARKER}\n\nhand written below\n"
    )
    run_dir("r1", 10.0, 5.0)
    summary, records, _ = render.load_run("r1")

    assert render.inject_into_readme(summary, records, [1, 3]) is True
    text = readme.read_text()
    assert "hand written above" in text
    assert "hand written below" in text
    assert "stale" not in text
    assert "Engine One" in text


def test_readme_without_markers_is_left_alone(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(render.paths, "ROOT", tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nno markers here\n")
    run_dir("r1", 10.0, 5.0)
    summary, records, _ = render.load_run("r1")

    assert render.inject_into_readme(summary, records, [1, 3]) is False
    assert readme.read_text() == "# Title\n\nno markers here\n"


def test_variance_flags_the_unstable_metric(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(variance.paths, "DOCS_DIR", tmp_path)
    run_dir("r1", 10.0, 5.0)
    run_dir("r2", 30.0, 5.2)  # load 3x apart, 3-hop nearly identical

    assert variance.build(["r1", "r2"]) == 0
    text = (tmp_path / "VARIANCE.md").read_text()
    load_row = next(line for line in text.splitlines() if line.startswith("| load total"))
    hop_row = next(line for line in text.splitlines() if line.startswith("| 3-hop p50"))
    assert "unstable" in load_row
    assert "unstable" not in hop_row


def test_variance_summarises_which_metrics_survive(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(variance.paths, "DOCS_DIR", tmp_path)
    run_dir("r1", 10.0, 5.0)
    run_dir("r2", 30.0, 5.2)
    variance.build(["r1", "r2"])
    text = (tmp_path / "VARIANCE.md").read_text()
    assert "Which metrics survive repetition" in text
    assert "load total (s) | 1 of 1" in text


def test_variance_needs_two_runs(tmp_path, monkeypatch, run_dir):
    monkeypatch.setattr(variance.paths, "RESULTS_DIR", tmp_path)
    run_dir("only", 10.0, 5.0)
    with pytest.raises(ValueError, match="at least 2"):
        variance.resolve(None)


def test_variance_defaults_to_every_completed_run(tmp_path, monkeypatch, run_dir):
    monkeypatch.setattr(variance.paths, "RESULTS_DIR", tmp_path)
    run_dir("r1", 10.0, 5.0)
    run_dir("r2", 11.0, 5.0)
    assert variance.resolve(None) == ["r1", "r2"]
