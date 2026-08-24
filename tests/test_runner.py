import json

import pytest

from graphbench import runner
from graphbench.config import Platform, Tier


@pytest.fixture
def no_docker(monkeypatch):
    """The fake platform has no container, but be explicit: no test should be able
    to destroy a real container."""
    monkeypatch.setattr(
        runner.dockerctl, "recreate", lambda *a, **k: pytest.fail("must not touch docker")
    )


@pytest.fixture
def wired(monkeypatch, adapter_factory):
    """Point the runner's factory at the fake engine."""
    made = {}

    def build(platform, graph, workloads, **kwargs):
        made["adapter"] = adapter_factory(**kwargs)
        return made["adapter"]

    monkeypatch.setattr(runner.adapters, "build", build)
    return made


def test_run_id_is_a_utc_timestamp():
    run_id = runner.new_run_id()
    assert run_id.endswith("Z")
    assert len(run_id) == 16


def test_a_clean_run_is_marked_ok(platform, graph, workloads, wired, no_docker):
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["status"] == "ok"
    assert record["errors"] == []
    assert record["load"]["phases"]
    assert record["reads"]
    assert record["duration_s"] >= 0


def test_reset_is_skipped_for_a_platform_with_no_container(
    platform, graph, workloads, wired, no_docker
):
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["reset"]["reset"] is False
    assert "not a local container" in record["reset"]["reason"]


def test_skip_mixed_is_recorded_rather_than_silently_absent(
    platform, graph, workloads, wired, no_docker
):
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["mixed"]["skipped"] is True


def test_mixed_runs_when_not_skipped(platform, graph, workloads, wired, no_docker):
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=False)
    assert record["mixed"]["levels"]
    assert record["mixed"]["read_ratio"] == workloads.read_ratio


def test_a_partial_load_downgrades_the_status(
    platform, graph, workloads, monkeypatch, adapter_factory, no_docker
):
    # One refused edge batch. The row must not read as clean.
    monkeypatch.setattr(
        runner.adapters,
        "build",
        lambda *a, **k: adapter_factory(fail_edge_batches={0}),
    )
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["status"] == "partial"
    assert record["errors"]


def test_connect_failure_is_captured_with_a_traceback(
    platform, graph, workloads, monkeypatch, adapter_factory, no_docker
):
    monkeypatch.setattr(
        runner.adapters,
        "build",
        lambda *a, **k: adapter_factory(fail_on={"connect": RuntimeError("no route to host")}),
    )
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["status"] == "failed"
    assert "no route to host" in record["errors"][0]
    assert "traceback" in record


def test_the_adapter_is_always_closed(platform, graph, workloads, wired, no_docker):
    runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert wired["adapter"].closed


def test_the_adapter_is_closed_even_when_the_run_fails(
    platform, graph, workloads, monkeypatch, adapter_factory, no_docker
):
    made = {}

    def build(*a, **k):
        made["adapter"] = adapter_factory(fail_on={"server_version": RuntimeError("x")})
        return made["adapter"]

    monkeypatch.setattr(runner.adapters, "build", build)
    runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert made["adapter"].closed


def test_tier_is_copied_into_the_record_for_the_results_table(
    graph, workloads, wired, no_docker
):
    platform = Platform(
        id="p",
        display="P",
        engine="fake",
        adapter="fake",
        track="cloud",
        tier=Tier(name="free", vcpu=None, ram_mb=1024, disk_gb=2, burstable=True,
                  source="https://example.com/pricing"),
    )
    record = runner.run_platform(platform, graph, workloads, "r1", skip_mixed=True)
    assert record["platform"]["tier"]["ram_mb"] == 1024
    assert record["platform"]["tier"]["vcpu"] is None
    assert record["platform"]["tier"]["source"].endswith("pricing")


def test_run_all_writes_one_file_per_platform_plus_a_summary(
    platform, graph, workloads, wired, no_docker, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner.paths, "RESULTS_DIR", tmp_path)
    summary = runner.run_all([platform], graph, workloads, skip_mixed=True, run_id="r9")

    out = tmp_path / "r9"
    assert (out / "fake-local.json").exists()
    assert (out / "run.json").exists()
    written = json.loads((out / "run.json").read_text())
    assert written["run_id"] == "r9"
    assert written["environment"]["python"]
    assert written["workloads"]["iterations"] == workloads.iterations
    assert summary["verification"]["clean"] is True


def test_unusable_platforms_are_recorded_as_skipped_not_run(
    graph, workloads, wired, no_docker, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner.paths, "RESULTS_DIR", tmp_path)
    unusable = Platform(
        id="needs-creds",
        display="Needs Creds",
        engine="fake",
        adapter="fake",
        track="cloud",
        tier=Tier(),
        missing_env=("SOME_URI",),
    )
    summary = runner.run_all([unusable], graph, workloads, skip_mixed=True, run_id="r9")
    assert summary["skipped"] == [{"id": "needs-creds", "reason": "missing SOME_URI"}]
    assert summary["platforms"] == []


def test_run_all_records_the_dataset_provenance(
    platform, graph, workloads, wired, no_docker, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner.paths, "RESULTS_DIR", tmp_path)
    summary = runner.run_all([platform], graph, workloads, skip_mixed=True, run_id="r9")
    assert summary["dataset"]["raw_sha256"] == "0" * 64
    assert summary["dataset"]["nodes"] == 5
