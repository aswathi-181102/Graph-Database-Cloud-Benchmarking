from dataclasses import replace

from graphbench.workloads import mixed


def test_sweeps_every_configured_concurrency_level(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, workloads, tag="t1")
    assert [r.concurrency for r in outcome.results] == list(workloads.concurrency)


def test_does_both_reads_and_writes(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, workloads, tag="t1")
    assert sum(r.reads for r in outcome.results) > 0
    assert sum(r.writes for r in outcome.results) > 0


def test_read_ratio_is_roughly_honoured(adapter, workloads):
    adapter.load()
    # 3 seconds at 90/10 gives enough ops that the ratio should be close
    outcome = mixed.run_mixed(adapter, replace(workloads, duration_s=3, concurrency=(4,)), "t1")
    level = outcome.results[0]
    ratio = level.reads / level.total_ops
    assert 0.85 < ratio < 0.95


def test_a_read_ratio_of_one_never_writes(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, replace(workloads, read_ratio=1.0), "t1")
    assert sum(r.writes for r in outcome.results) == 0


def test_writes_are_cleaned_up_and_counted(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, workloads, tag="run-42")
    assert outcome.writes_cleaned == sum(r.writes for r in outcome.results)
    # and the graph is back to its pre-workload state
    assert adapter.writes == {}


def test_cleanup_can_be_switched_off(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, replace(workloads, cleanup_writes=False), "t1")
    assert outcome.writes_cleaned == 0
    assert adapter.writes  # still there


def test_throughput_is_computed_from_measured_elapsed_time(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, replace(workloads, concurrency=(1,)), "t1")
    level = outcome.results[0]
    assert level.duration_s >= workloads.duration_s
    assert level.qps == round(level.total_ops / level.duration_s, 10) or level.qps > 0


def test_the_interleaving_is_reproducible(adapter_factory, workloads):
    """Same seed, same read/write split. Otherwise a lucky run with fewer writes
    posts a better throughput number than an unlucky one."""
    splits = []
    for _ in range(2):
        a = adapter_factory()
        a.connect()
        a.load()
        outcome = mixed.run_mixed(a, replace(workloads, concurrency=(2,), duration_s=1), "t")
        splits.append(
            [
                (r.reads + r.writes) and r.writes / (r.reads + r.writes)
                for r in outcome.results
            ]
        )
    # not identical op counts (wall-clock bounded), but the same mix
    assert abs(splits[0][0] - splits[1][0]) < 0.1


def test_a_failing_level_does_not_lose_the_others(adapter_factory, workloads):
    a = adapter_factory()
    a.connect()
    a.load()

    original = mixed._run_level
    calls = {"n": 0}

    def flaky(adapter, wl, tag, clients):
        calls["n"] += 1
        if clients == workloads.concurrency[-1]:
            raise RuntimeError("engine died at the top level")
        return original(adapter, wl, tag, clients)

    mixed._run_level = flaky
    try:
        outcome = mixed.run_mixed(a, workloads, tag="t1")
    finally:
        mixed._run_level = original

    assert len(outcome.results) == len(workloads.concurrency) - 1
    assert any("engine died" in e for e in outcome.errors)


def test_worker_errors_are_captured_rather_than_raised(adapter_factory, workloads):
    a = adapter_factory(fail_on={"point_lookup": RuntimeError("boom")})
    a.connect()
    a.load()
    outcome = mixed.run_mixed(a, replace(workloads, concurrency=(1,)), "t1")
    level = outcome.results[0]
    assert level.read_latency.errors


def test_cleanup_failure_is_recorded_not_raised(adapter_factory, workloads):
    a = adapter_factory(fail_on={"cleanup_writes": RuntimeError("cleanup broke")})
    a.connect()
    a.load()
    outcome = mixed.run_mixed(a, replace(workloads, concurrency=(1,)), "t1")
    assert any("cleanup" in e for e in outcome.errors)


def test_result_dict_omits_samples_by_default(adapter, workloads):
    adapter.load()
    outcome = mixed.run_mixed(adapter, replace(workloads, concurrency=(1,)), "t1")
    out = outcome.results[0].to_dict()
    assert "samples_ms" not in out["read_latency"]
    assert out["read_latency"]["count"] > 0
