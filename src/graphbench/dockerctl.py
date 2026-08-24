"""Reset the local containers to a virgin state before each run.

This exists because of two real failures, found in this order.

First, Memgraph loaded all 198,050 edges into a fresh container and then failed at
5,000 edges on the next run in the same process:

    Memory limit exceeded! Current use is 202.86MiB, while the maximum allowed
    size for allocation is set to 200.00MiB

wipe() had deleted the data and reported success, but Memgraph does not hand freed
memory back to its allocator, so the reload began with the budget already spent.

So the runner started restarting containers. That was still not enough. Memgraph
writes a snapshot on exit, even with periodic snapshots and the WAL disabled, so
`docker restart` recovered the previous run's graph on boot and the reload OOMed
again, this time at 15,000 nodes with zero relationships. The cross-platform
verification caught it: Memgraph answered 0 where the other three answered 42.

Hence recreate() rather than restart(): remove the container, delete its volumes,
bring it back. Every engine then begins its load against a genuinely empty store
with nothing pre-allocated, which is the only way ingest numbers are comparable.

The cloud platforms cannot be reset this way, which is a stated asymmetry: their
load numbers are measured against whatever state the managed service was in.
"""

import shutil
import subprocess
import time

from graphbench import paths


class DockerUnavailable(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=timeout, cwd=paths.ROOT
    )


def available() -> bool:
    if not shutil.which("docker"):
        return False
    return _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30).returncode == 0


def _inspect(container: str, template: str) -> str | None:
    out = _run(["docker", "inspect", "--format", template, container], timeout=30)
    return out.stdout.strip() if out.returncode == 0 else None


def volumes_of(container: str) -> list[str]:
    """Named volumes attached to a container, so they can be deleted with it."""
    out = _inspect(
        container,
        '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}',
    )
    return out.split() if out else []


def recreate(service: str, container: str, timeout: int = 300) -> dict[str, object]:
    """Destroy and rebuild a compose service, volumes included."""
    if not available():
        raise DockerUnavailable("docker daemon is not reachable")

    began = time.perf_counter()
    # Read the volume list before removing the container; afterwards there is
    # nothing left to inspect.
    volumes = volumes_of(container)

    rm = _run(["docker", "compose", "rm", "-sf", service], timeout=timeout)
    if rm.returncode != 0:
        raise DockerUnavailable(f"compose rm {service} failed: {rm.stderr.strip()}")

    removed = []
    for volume in volumes:
        if _run(["docker", "volume", "rm", volume], timeout=60).returncode == 0:
            removed.append(volume)

    up = _run(["docker", "compose", "up", "-d", "--wait", service], timeout=timeout)
    if up.returncode != 0:
        raise DockerUnavailable(f"compose up {service} failed: {up.stderr.strip()}")

    healthy = wait_healthy(container, timeout=timeout)
    return {
        "reset": True,
        "method": "compose rm -sf + volume rm + up",
        "volumes_removed": removed,
        "healthy": healthy,
        "seconds_to_healthy": round(time.perf_counter() - began, 1),
    }


def wait_healthy(container: str, timeout: int = 300, poll: float = 2.0) -> bool:
    """Poll until the healthcheck passes.

    Falls back to "is it running" where no healthcheck is defined, rather than
    waiting the full timeout for a status that will never arrive.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = _inspect(container, "{{if .State.Health}}{{.State.Health.Status}}{{end}}")
        if health == "healthy":
            return True
        if health in (None, "") and _inspect(container, "{{.State.Running}}") == "true":
            return True
        # "unhealthy" is normal during startup, since the probe runs before the
        # engine is listening. Keep waiting.
        time.sleep(poll)
    return False
