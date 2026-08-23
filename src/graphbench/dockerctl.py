"""Restart and wait on the local containers.

This exists because of a real result. Memgraph loaded all 198,050 edges into a
freshly started container, then failed at 5,000 edges on the next run in the same
process:

    Memory limit exceeded! Current use is 202.86MiB, while the maximum allowed
    size for allocation is set to 200.00MiB

wipe() had deleted the data and reported success, but Memgraph does not hand freed
memory back to its allocator, so the reload started with the budget already spent.

Two consequences. First, that is a finding worth reporting in its own right: at
256 MB, Memgraph cannot reload this dataset without a process restart. Second, it
means load numbers are only comparable if every engine starts from a fresh
process, so the local track restarts each container before its run.

The cloud platforms cannot be restarted, which is a stated asymmetry: their load
numbers are measured against whatever state the managed service was in.
"""

import shutil
import subprocess
import time


class DockerUnavailable(RuntimeError):
    pass


def available() -> bool:
    if not shutil.which("docker"):
        return False
    out = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return out.returncode == 0


def _inspect(container: str, template: str) -> str | None:
    out = subprocess.run(
        ["docker", "inspect", "--format", template, container],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def restart(container: str, timeout: int = 180) -> dict[str, object]:
    """Restart a container and wait until it is healthy again.

    Returns how long it took, which doubles as a cold-start-to-ready figure.
    """
    if not available():
        raise DockerUnavailable("docker daemon is not reachable")

    began = time.perf_counter()
    out = subprocess.run(
        ["docker", "restart", container],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise DockerUnavailable(f"docker restart {container} failed: {out.stderr.strip()}")

    healthy = wait_healthy(container, timeout=timeout)
    return {
        "restarted": True,
        "healthy": healthy,
        "seconds_to_healthy": round(time.perf_counter() - began, 1),
    }


def wait_healthy(container: str, timeout: int = 180, poll: float = 2.0) -> bool:
    """Poll until the container's healthcheck passes.

    Falls back to "is it running" for a container with no healthcheck defined,
    rather than waiting the full timeout for a status that will never appear.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = _inspect(container, "{{if .State.Health}}{{.State.Health.Status}}{{end}}")
        if health == "healthy":
            return True
        if health in (None, ""):
            running = _inspect(container, "{{.State.Running}}")
            if running == "true":
                return True
        if health == "unhealthy":
            # Keep waiting: unhealthy is normal during startup, since the probe
            # runs before the engine is listening.
            pass
        time.sleep(poll)
    return False
