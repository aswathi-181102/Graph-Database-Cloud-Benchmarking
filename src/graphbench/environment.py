"""Machine and toolchain metadata, recorded with every run.

A latency number without the client it was measured from is not reproducible, and
the assignment asks for environment specs anyway. Collected once per run.
"""

import platform as py_platform
import shutil
import subprocess
import sys
from importlib import metadata
from typing import Any

# Only the ones that sit in the measured path.
TRACKED_PACKAGES = ("neo4j", "FalkorDB", "python-arango", "kuzu")


def _cmd(args: list[str]) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _package_versions() -> dict[str, str]:
    versions = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def _physical_memory_bytes() -> int | None:
    # No stdlib way to get this. sysctl on macOS, /proc/meminfo on Linux.
    out = _cmd(["sysctl", "-n", "hw.memsize"])
    if out and out.isdigit():
        return int(out)
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def describe() -> dict[str, Any]:
    return {
        "client_os": f"{py_platform.system()} {py_platform.release()}",
        "client_machine": py_platform.machine(),
        "client_processor": py_platform.processor() or _cmd(
            ["sysctl", "-n", "machdep.cpu.brand_string"]
        ),
        "client_cpu_count": _cpu_count(),
        "client_memory_bytes": _physical_memory_bytes(),
        "python": sys.version.split()[0],
        "packages": _package_versions(),
        "docker": _cmd(["docker", "--version"]),
        # Which commit produced these numbers. Without it a results file cannot be
        # traced back to the code that made it.
        "git_commit": _cmd(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(_cmd(["git", "status", "--porcelain"])),
    }


def _cpu_count() -> int | None:
    import os

    # Prefer the scheduler's view; on a container-limited host it differs from the
    # hardware count and the scheduler's answer is the one that matters.
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count()


def container_stats(name: str, data_dir: str = "") -> dict[str, Any]:
    """Real RSS and CPU for a running container, read from outside the engine.

    This is the only honest footprint figure for engines that expose nothing
    themselves, and it is the one place a "not observable" can be turned into a
    number, so it is worth the subprocess.
    """
    out = _cmd(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}|{{.MemPerc}}|{{.CPUPerc}}",
            name,
        ]
    )
    if not out:
        return {"observable": False, "reason": f"docker stats returned nothing for {name}"}
    parts = out.split("|")
    if len(parts) != 3:
        return {"observable": False, "reason": f"unexpected docker stats output: {out!r}"}

    stats: dict[str, Any] = {
        "observable": True,
        "source": "docker stats",
        "container": name,
        "mem_usage": parts[0],
        "mem_percent": parts[1],
        "cpu_percent": parts[2],
    }
    if data_dir:
        # du -sb is GNU; the alpine-based images ship BusyBox, which needs -sk and
        # reports kilobytes. Try both rather than guessing per image.
        raw = _cmd(["docker", "exec", name, "sh", "-c", f"du -sb {data_dir} 2>/dev/null | cut -f1"])
        if raw and raw.isdigit():
            stats["data_dir_bytes"] = int(raw)
        else:
            kb = _cmd(
                ["docker", "exec", name, "sh", "-c", f"du -sk {data_dir} 2>/dev/null | cut -f1"]
            )
            if kb and kb.isdigit():
                stats["data_dir_bytes"] = int(kb) * 1024
        stats["data_dir"] = data_dir
    return stats
