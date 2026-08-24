import pytest

from graphbench.metrics import LatencySeries, ThroughputResult, Timer


def series(values):
    s = LatencySeries()
    for v in values:
        s.add(v)
    return s


def test_percentiles_are_nearest_rank_not_interpolated():
    # 1..100. Nearest rank means p95 is the 95th smallest value, an observation
    # that actually happened, not an average of the 95th and 96th.
    s = series(range(1, 101))
    assert s.p50 == 50
    assert s.p95 == 95
    assert s.p99 == 99


def test_percentile_of_a_single_sample_is_that_sample():
    s = series([7.5])
    assert s.p50 == 7.5
    assert s.p95 == 7.5


def test_percentiles_are_none_with_no_samples():
    s = LatencySeries()
    assert s.p50 is None
    assert s.p95 is None
    assert s.mean is None


def test_percentile_does_not_index_below_zero():
    # ceil(0 * n) is 0, which would be a rank of 0 and index -1, silently
    # returning the largest sample instead of the smallest.
    s = series([3, 1, 2])
    assert s.percentile(0.0) == 1


def test_samples_are_not_reordered_in_place():
    # percentile() sorts a copy; sorting in place would silently reorder the raw
    # samples that get written to the results file.
    s = series([3, 1, 2])
    assert s.p50 == 2
    assert s.samples == [3, 1, 2]


def test_stdev_needs_two_samples():
    assert series([1]).stdev is None
    assert series([1, 3]).stdev == pytest.approx(1.4142, abs=1e-3)


def test_failures_are_recorded_not_raised():
    s = LatencySeries()
    s.fail(ValueError("boom"))
    s.fail(TimeoutError("slow"), timed_out=True)
    assert s.timeouts == 1
    assert len(s.errors) == 2
    assert "ValueError: boom" in s.errors[0]


def test_to_dict_keeps_raw_samples_when_asked():
    s = series([1.0, 2.0])
    assert s.to_dict(include_samples=True)["samples_ms"] == [1.0, 2.0]
    assert "samples_ms" not in s.to_dict(include_samples=False)


def test_to_dict_caps_stored_error_messages():
    s = LatencySeries()
    for i in range(30):
        s.fail(ValueError(f"e{i}"))
    out = s.to_dict()
    assert out["errors"] == 30
    assert len(out["error_samples"]) == 5


def test_timer_measures_in_milliseconds():
    import time

    with Timer() as t:
        time.sleep(0.02)
    assert 15 < t.elapsed_ms < 200  # generous: CI machines stall


def test_throughput_divides_by_measured_elapsed_not_requested_duration():
    r = ThroughputResult(
        concurrency=10,
        duration_s=30.5,
        reads=900,
        writes=100,
        read_latency=series([1.0]),
        write_latency=series([2.0]),
    )
    assert r.total_ops == 1000
    assert r.qps == pytest.approx(1000 / 30.5)


def test_throughput_with_zero_duration_does_not_divide_by_zero():
    r = ThroughputResult(0, 0.0, 0, 0, LatencySeries(), LatencySeries())
    assert r.qps == 0.0
