"""Charts. Log scale where the spread demands it, which is most of the time."""

from pathlib import Path
from typing import Any

import matplotlib

# Agg before pyplot: this runs headless, and the default backend tries to find a
# display and fails.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Colourblind-safe. The traversal chart puts three series next to each other and
# red/green would be the obvious choice and the wrong one.
COLOURS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"]


def _label(record: dict) -> str:
    return record["platform"]["display"].replace(" (capped)", "")


def _read(record: dict, name: str, stat: str = "p50_ms") -> float | None:
    workload = next((r for r in record.get("reads", []) if r["name"] == name), None)
    return workload["latency"].get(stat) if workload else None


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def traversal_chart(records: list[dict], hops: list[int], out: Path) -> Path | None:
    usable = [r for r in records if any(_read(r, f"traversal_{h}hop") for h in hops)]
    if not usable:
        return None

    fig, ax = plt.subplots(figsize=(9, 4.5))
    width = 0.8 / len(hops)
    positions = range(len(usable))

    for i, hop in enumerate(hops):
        values = [_read(r, f"traversal_{hop}hop") or 0 for r in usable]
        offsets = [p + i * width - 0.4 + width / 2 for p in positions]
        ax.bar(offsets, values, width, label=f"{hop}-hop", color=COLOURS[i % len(COLOURS)])

    ax.set_xticks(list(positions))
    ax.set_xticklabels([_label(r) for r in usable], rotation=20, ha="right")
    ax.set_ylabel("p50 latency (ms), log scale")
    # Log because 1-hop and 3-hop are often two orders of magnitude apart, and on a
    # linear axis every 1-hop bar would be an invisible sliver.
    ax.set_yscale("log")
    ax.set_title("Traversal latency by depth (p50)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, which="both")
    return _save(fig, out / "traversal_latency.png")


def tail_chart(records: list[dict], out: Path) -> Path | None:
    """p50 against p95 for one workload, because the gap is the throttling story."""
    names = ["point_lookup", "traversal_2hop", "aggregation_groupby_cohort"]
    usable = [r for r in records if _read(r, "point_lookup")]
    if not usable:
        return None

    fig, axes = plt.subplots(1, len(names), figsize=(13, 4.2), sharey=False)
    for ax, name in zip(axes, names, strict=True):
        labels = [_label(r) for r in usable]
        p50 = [_read(r, name, "p50_ms") or 0 for r in usable]
        p95 = [_read(r, name, "p95_ms") or 0 for r in usable]
        positions = range(len(usable))
        ax.bar([p - 0.2 for p in positions], p50, 0.4, label="p50", color=COLOURS[0])
        ax.bar([p + 0.2 for p in positions], p95, 0.4, label="p95", color=COLOURS[1])
        ax.set_xticks(list(positions))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(name.replace("_", " "), fontsize=10)
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3, which="both")
    axes[0].set_ylabel("latency (ms), log scale")
    axes[0].legend()
    return _save(fig, out / "p50_vs_p95.png")


def ingest_chart(records: list[dict], out: Path) -> Path | None:
    usable = [r for r in records if r.get("load")]
    if not usable:
        return None

    fig, ax = plt.subplots(figsize=(9, 4.5))
    positions = range(len(usable))
    nodes = [r["load"].get("nodes_per_second") or 0 for r in usable]
    rels = [r["load"].get("rels_per_second") or 0 for r in usable]
    ax.bar([p - 0.2 for p in positions], nodes, 0.4, label="nodes/s", color=COLOURS[0])
    ax.bar([p + 0.2 for p in positions], rels, 0.4, label="rels/s", color=COLOURS[2])
    ax.set_xticks(list(positions))
    ax.set_xticklabels([_label(r) for r in usable], rotation=20, ha="right")
    ax.set_ylabel("rows per second, log scale")
    ax.set_yscale("log")
    ax.set_title("Ingest throughput")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, which="both")
    return _save(fig, out / "ingest_throughput.png")


def concurrency_chart(records: list[dict], out: Path) -> Path | None:
    usable = [r for r in records if r.get("mixed", {}).get("levels")]
    if not usable:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, record in enumerate(usable):
        levels = sorted(record["mixed"]["levels"], key=lambda lv: lv["concurrency"])
        ax.plot(
            [lv["concurrency"] for lv in levels],
            [lv["qps"] for lv in levels],
            marker="o",
            label=_label(record),
            color=COLOURS[i % len(COLOURS)],
        )
    ax.set_xlabel("concurrent clients")
    ax.set_ylabel("sustained queries/second")
    ax.set_title("Mixed workload throughput (90% read / 10% write)")
    ax.grid(alpha=0.3)
    ax.legend()
    return _save(fig, out / "concurrency_sweep.png")


def build_all(records: list[dict], hops: list[int], out: Path) -> list[dict[str, Any]]:
    charts = []
    for name, fn in (
        ("traversal", lambda: traversal_chart(records, hops, out)),
        ("tail", lambda: tail_chart(records, out)),
        ("ingest", lambda: ingest_chart(records, out)),
        ("concurrency", lambda: concurrency_chart(records, out)),
    ):
        path = fn()
        if path:
            charts.append({"name": name, "path": str(path)})
    return charts
