from dataclasses import replace

from graphbench.workloads import reads


def run(name, call, workloads, **kwargs):
    return reads.run_read_workload(name, call, workloads, **kwargs)


def test_measures_exactly_the_requested_iterations(workloads):
    calls = []
    result = run("x", lambda i: (calls.append(i) or 1, "value"), workloads)
    assert len(result.latency) == workloads.iterations
    # warmup runs too, but is not measured
    assert len(calls) == workloads.iterations + workloads.warmup


def test_first_warmup_call_is_kept_separately(workloads):
    result = run("x", lambda i: (1, "value"), workloads)
    assert result.first_call_ms is not None
    # and it is not one of the measured samples
    assert len(result.latency) == workloads.iterations


def test_no_warmup_means_no_first_call_figure(workloads):
    result = run("x", lambda i: (1, "value"), replace(workloads, warmup=0))
    assert result.first_call_ms is None


def test_returned_values_are_captured_for_cross_checking(workloads):
    result = run("x", lambda i: (i * 2, f"k{i % 3}"), workloads)
    assert set(result.checks) == {"k0", "k1", "k2"}


def test_first_value_per_key_wins_so_checks_are_stable(workloads):
    # setdefault, not assignment: otherwise the recorded value depends on iteration
    # count and two platforms with different iteration counts could not be compared.
    seq = iter(range(1000))
    run("x", lambda i: (next(seq), "same"), workloads)
    result = run("y", lambda i: (i, "same"), workloads)
    assert result.checks["same"] == workloads.warmup


def test_soft_failures_are_counted_and_the_loop_continues(workloads):
    def call(i):
        if i % 10 == 0:
            raise ValueError("transient")
        return 1, "value"

    result = run("x", call, workloads)
    assert not result.abandoned
    assert result.latency.errors
    # everything that did not raise still produced a sample
    assert len(result.latency) == workloads.iterations - workloads.iterations // 10


def test_out_of_memory_abandons_the_workload(workloads):
    def call(i):
        if i > 5:
            raise RuntimeError("Memory limit exceeded! Current use is 202.86MiB")
        return 1, "value"

    result = run("x", call, workloads)
    assert result.abandoned
    assert len(result.latency) < workloads.iterations


def test_a_dead_connection_abandons_during_warmup(workloads):
    def call(i):
        raise RuntimeError("Failed to read from defunct connection")

    result = run("x", call, workloads)
    assert result.abandoned
    assert len(result.latency) == 0


def test_overrunning_the_ceiling_records_a_timeout_and_stops(workloads):
    import time

    def call(i):
        time.sleep(0.05)
        return 1, "value"

    result = run("x", call, replace(workloads, timeout_s=0, warmup=0))
    assert result.abandoned
    assert result.latency.timeouts == 1
    # the overrun is not folded into the percentiles as an ordinary sample
    assert len(result.latency) == 1


def test_expected_value_mismatch_is_recorded(workloads):
    result = run("x", lambda i: (41, "value"), workloads, expected=42)
    out = result.to_dict()
    assert out["expected"] == 42
    assert out["matches_expected"] is False


def test_expected_value_match_is_recorded(workloads):
    result = run("x", lambda i: (42, "value"), workloads, expected=42)
    assert result.to_dict()["matches_expected"] is True


def test_observed_value_is_none_when_many_keys_were_touched(workloads):
    result = run("x", lambda i: (i, f"k{i}"), workloads)
    assert result.observed_value is None


def test_fatal_classifier():
    assert reads._is_fatal(RuntimeError("Java heap space"))
    assert reads._is_fatal(RuntimeError("OutOfMemoryError"))
    assert reads._is_fatal(RuntimeError("not enough memory"))
    assert reads._is_fatal(RuntimeError("defunct connection"))
    assert not reads._is_fatal(ValueError("bad parameter"))
    assert not reads._is_fatal(TimeoutError("slow query"))


def test_all_read_workloads_covers_every_required_metric(adapter, workloads):
    adapter.load()
    results = reads.all_read_workloads(adapter, workloads)
    names = [r.name for r in results]

    assert "baseline_noop" in names
    assert "point_lookup" in names
    assert "filtered_lookup" in names
    assert "aggregation_groupby_cohort" in names
    assert "aggregation_rel_count" in names
    for hop in workloads.hops:
        assert f"traversal_{hop}hop" in names


def test_baseline_can_be_switched_off(adapter, workloads):
    adapter.load()
    results = reads.all_read_workloads(adapter, replace(workloads, baseline_query=False))
    assert "baseline_noop" not in [r.name for r in results]


def test_aggregations_carry_their_known_correct_answers(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    assert results["aggregation_groupby_cohort"].expected == adapter.graph.node_count
    assert results["aggregation_rel_count"].expected == adapter.graph.edge_count
    assert results["aggregation_groupby_cohort"].to_dict()["matches_expected"] is True
    assert results["aggregation_rel_count"].to_dict()["matches_expected"] is True


def test_start_keys_rotate_rather_than_repeating(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    # 5 start keys in the fixture, and a 100-iteration run should touch all of them
    assert len(results["point_lookup"].checks) == len(adapter.graph.start_keys)


def test_traversal_counts_are_monotonic_in_depth(adapter, workloads):
    adapter.load()
    results = {r.name: r for r in reads.all_read_workloads(adapter, workloads)}
    one = results["traversal_1hop"].checks
    two = results["traversal_2hop"].checks
    for key in set(one) & set(two):
        assert one[key] <= two[key]
