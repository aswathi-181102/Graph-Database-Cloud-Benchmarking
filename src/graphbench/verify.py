"""Cross-platform agreement check.

Every read workload returns a value, and for the same input every platform has to
return the same value. If they do not, the queries are not equivalent and no
timing comparison between them means anything, however fast one of them looked.

This is the check that would have caught a dropped relationship direction, a
traversal depth off by one, or ArangoDB's default path-uniqueness semantics
quietly answering a different question than Cypher's.
"""

from typing import Any


def compare(records: list[dict[str, Any]]) -> dict[str, Any]:
    # workload -> check key -> {platform id: value}
    observed: dict[str, dict[str, dict[str, int]]] = {}

    for record in records:
        pid = record["platform"]["id"]
        for workload in record.get("reads", []):
            for key, value in (workload.get("checks") or {}).items():
                observed.setdefault(workload["name"], {}).setdefault(key, {})[pid] = value

    mismatches = []
    compared = 0
    for workload, keys in sorted(observed.items()):
        for key, by_platform in sorted(keys.items()):
            if len(by_platform) < 2:
                # Only one platform answered, so there is nothing to disagree with.
                continue
            compared += 1
            if len(set(by_platform.values())) > 1:
                mismatches.append({"workload": workload, "key": key, "values": by_platform})

    # Workloads with a known correct answer (the group-by must sum to the node
    # count, the relationship count must equal the edge count) are checked
    # separately: those can be wrong on every platform at once, which agreement
    # alone would never reveal.
    expectation_failures = []
    for record in records:
        pid = record["platform"]["id"]
        for workload in record.get("reads", []):
            if workload.get("expected") is None:
                continue
            if workload.get("matches_expected") is False:
                expectation_failures.append(
                    {
                        "platform": pid,
                        "workload": workload["name"],
                        "expected": workload["expected"],
                        "got": (workload.get("checks") or {}).get("value"),
                    }
                )

    monotonic_failures = _check_monotonic_hops(records)

    return {
        "compared": compared,
        "agree": not mismatches,
        "mismatches": mismatches,
        "expectation_failures": expectation_failures,
        "monotonic_failures": monotonic_failures,
        "clean": not (mismatches or expectation_failures or monotonic_failures),
    }


def _check_monotonic_hops(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1-hop <= 2-hop <= 3-hop, per start node, per platform.

    Free because of how the traversal is defined as a k-hop neighbourhood, and it
    catches a depth mix-up on a single platform without needing a second platform
    to compare against.
    """
    failures = []
    for record in records:
        pid = record["platform"]["id"]
        by_depth: dict[int, dict[str, int]] = {}
        for workload in record.get("reads", []):
            name = workload["name"]
            if not name.startswith("traversal_"):
                continue
            depth = int(name.removeprefix("traversal_").removesuffix("hop"))
            by_depth[depth] = workload.get("checks") or {}

        depths = sorted(by_depth)
        for lower, upper in zip(depths, depths[1:], strict=False):
            shared = set(by_depth[lower]) & set(by_depth[upper])
            for key in sorted(shared):
                if by_depth[lower][key] > by_depth[upper][key]:
                    failures.append(
                        {
                            "platform": pid,
                            "key": key,
                            f"{lower}hop": by_depth[lower][key],
                            f"{upper}hop": by_depth[upper][key],
                        }
                    )
    return failures
