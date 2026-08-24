from graphbench import verify


def record(pid, reads):
    return {"platform": {"id": pid}, "reads": reads}


def read(name, checks, expected=None, matches=None):
    out = {"name": name, "checks": checks, "latency": {}}
    if expected is not None:
        out["expected"] = expected
        out["matches_expected"] = matches
    return out


def test_agreement_across_platforms():
    r = verify.compare(
        [
            record("a", [read("traversal_2hop", {"a1": 49, "a2": 795})]),
            record("b", [read("traversal_2hop", {"a1": 49, "a2": 795})]),
        ]
    )
    assert r["agree"]
    assert r["clean"]
    assert r["compared"] == 2


def test_disagreement_is_reported_with_both_values():
    r = verify.compare(
        [
            record("a", [read("traversal_2hop", {"a1": 49})]),
            record("b", [read("traversal_2hop", {"a1": 48})]),
        ]
    )
    assert not r["agree"]
    assert not r["clean"]
    assert r["mismatches"][0]["values"] == {"a": 49, "b": 48}


def test_a_single_platform_has_nothing_to_compare():
    r = verify.compare([record("a", [read("traversal_2hop", {"a1": 49})])])
    assert r["compared"] == 0
    assert r["agree"]  # vacuously
    assert r["clean"]


def test_keys_only_one_platform_answered_are_not_compared():
    r = verify.compare(
        [
            record("a", [read("point_lookup", {"a1": 5, "a2": 7})]),
            record("b", [read("point_lookup", {"a1": 5})]),
        ]
    )
    assert r["compared"] == 1
    assert r["agree"]


def test_known_answer_failure_is_caught_even_when_platforms_agree():
    # Both wrong in the same way. Agreement alone would call this clean.
    r = verify.compare(
        [
            record("a", [read("aggregation_rel_count", {"value": 5000}, 198050, False)]),
            record("b", [read("aggregation_rel_count", {"value": 5000}, 198050, False)]),
        ]
    )
    assert r["agree"]
    assert not r["clean"]
    assert r["expectation_failures"][0]["got"] == 5000


def test_non_monotonic_hops_are_caught_on_one_platform():
    r = verify.compare(
        [
            record(
                "a",
                [
                    read("traversal_1hop", {"a1": 50}),
                    read("traversal_2hop", {"a1": 40}),  # cannot shrink
                ],
            )
        ]
    )
    assert not r["clean"]
    assert r["monotonic_failures"][0]["key"] == "a1"


def test_monotonic_check_tolerates_equal_counts():
    # Equal is fine: a node whose whole component is within 1 hop.
    r = verify.compare(
        [
            record(
                "a",
                [read("traversal_1hop", {"a1": 5}), read("traversal_2hop", {"a1": 5})],
            )
        ]
    )
    assert r["clean"]


def test_platform_that_failed_entirely_is_ignored_not_crashed_on():
    r = verify.compare(
        [
            {"platform": {"id": "dead"}, "errors": ["boom"]},
            record("b", [read("traversal_2hop", {"a1": 49})]),
        ]
    )
    assert r["clean"]
