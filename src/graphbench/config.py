"""Load config/platforms.yaml and config/workloads.yaml.

The one interesting thing in here is env var substitution. Credentials live in
the environment and the YAML only holds ${VAR} references, which buys three
things:

  1. config/platforms.yaml is safe to commit, and the assignment explicitly
     forbids committing connection URIs or passwords.
  2. A platform with no credentials becomes "skipped: missing COGNODB_URI"
     instead of a stack trace on connect, so a partial run is a normal outcome.
     That matters because nobody reproducing this will have accounts on all
     nine entries.
  3. The set of platforms is data, not code. Adding a sixth engine is a YAML
     block plus an adapter, with no changes to the runner.

Empty is treated as missing on purpose. `COGNODB_URI=` in a .env file is what
you get from copying .env.example and not filling it in, and treating that as
"configured" would mean a confusing connection failure later instead of a clear
skip now.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from graphbench import paths

# ${VAR} is required. ${VAR:-fallback} is optional with a default. Same shape as
# shell parameter expansion so it reads the way people expect, but deliberately
# not passed through a shell.
ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


@dataclass(frozen=True)
class Tier:
    """Advertised resources. null means the vendor does not publish it.

    Kept as declared data rather than probed, because for a managed free tier
    the advertised spec is the only thing you can cite, and citing it is what
    the assignment asks for. Where a value is None the README says "not
    published" rather than guessing.
    """

    name: str = ""
    vcpu: float | None = None
    ram_mb: int | None = None
    disk_gb: int | None = None
    burstable: bool = False
    notes: str = ""
    source: str = ""

    def summary(self) -> str:
        cpu = "?" if self.vcpu is None else f"{self.vcpu:g}"
        ram = "?" if self.ram_mb is None else f"{self.ram_mb} MB"
        disk = "?" if self.disk_gb is None else f"{self.disk_gb} GB"
        burst = ", burstable" if self.burstable else ""
        return f"{cpu} vCPU / {ram} / {disk}{burst}"


@dataclass(frozen=True)
class Platform:
    id: str
    display: str
    engine: str
    adapter: str
    track: str
    tier: Tier
    connection: dict[str, Any] = field(default_factory=dict)
    # Which ${VAR}s came back empty. Non-empty means this platform gets skipped.
    missing_env: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.missing_env

    @property
    def skip_reason(self) -> str:
        return "missing " + ", ".join(self.missing_env)


@dataclass(frozen=True)
class Workloads:
    batch_size: int
    iterations: int
    warmup: int
    baseline_query: bool
    hops: tuple[int, ...]
    timeout_s: int
    concurrency: tuple[int, ...]
    duration_s: int
    read_ratio: float
    cleanup_writes: bool
    cohort: int
    min_degree: int

    def __post_init__(self) -> None:
        # Cheap invariants, checked once at load. A benchmark that runs for
        # twenty minutes and then reports a p95 over 3 samples is worse than one
        # that refuses to start.
        if self.iterations < 100:
            raise ValueError(
                f"iterations={self.iterations} is below the 100 the assignment asks for"
            )
        if not 0.0 <= self.read_ratio <= 1.0:
            raise ValueError(f"read_ratio must be a fraction, got {self.read_ratio}")
        if not self.hops:
            raise ValueError("at least one hop depth is required")


def _resolve(value: Any, missing: list[str]) -> Any:
    """Replace a ${VAR} string with its env value. Everything else passes through."""
    if not isinstance(value, str):
        return value
    m = ENV_REF.match(value.strip())
    if not m:
        return value

    name, default = m.group(1), m.group(2)
    env = os.environ.get(name, "")
    if env:
        return env
    if default is not None:
        return default
    missing.append(name)
    return ""


def _resolve_tree(node: Any, missing: list[str]) -> Any:
    if isinstance(node, dict):
        return {k: _resolve_tree(v, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_tree(v, missing) for v in node]
    return _resolve(node, missing)


def load_platforms(path: Path | None = None) -> list[Platform]:
    raw = yaml.safe_load((path or paths.PLATFORMS_FILE).read_text())
    out: list[Platform] = []

    for entry in raw.get("platforms", []):
        missing: list[str] = []
        conn = _resolve_tree(entry.get("connection", {}), missing)
        tier_data = _resolve_tree(entry.get("tier", {}), missing)

        out.append(
            Platform(
                id=entry["id"],
                display=entry.get("display", entry["id"]),
                engine=entry["engine"],
                adapter=entry["adapter"],
                track=entry.get("track", "cloud"),
                tier=Tier(**tier_data),
                connection=conn,
                missing_env=tuple(dict.fromkeys(missing)),  # dedupe, keep order
            )
        )

    ids = [p.id for p in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        # Duplicate ids would silently overwrite each other's result files.
        raise ValueError(f"duplicate platform ids in config: {sorted(dupes)}")
    return out


def load_workloads(path: Path | None = None) -> Workloads:
    raw = yaml.safe_load((path or paths.WORKLOADS_FILE).read_text())
    load, reads = raw["load"], raw["reads"]
    mixed, lookups = raw["mixed"], raw["lookups"]

    return Workloads(
        batch_size=int(load["batch_size"]),
        iterations=int(reads["iterations"]),
        warmup=int(reads["warmup"]),
        baseline_query=bool(reads.get("baseline_query", True)),
        hops=tuple(int(h) for h in reads["hops"]),
        timeout_s=int(reads["timeout_s"]),
        concurrency=tuple(int(c) for c in mixed["concurrency"]),
        duration_s=int(mixed["duration_s"]),
        read_ratio=float(mixed["read_ratio"]),
        cleanup_writes=bool(mixed.get("cleanup_writes", True)),
        cohort=int(lookups["cohort"]),
        min_degree=int(lookups["min_degree"]),
    )


def default_dataset(path: Path | None = None) -> str:
    raw = yaml.safe_load((path or paths.PLATFORMS_FILE).read_text())
    return raw.get("defaults", {}).get("dataset", "ca-astroph")
