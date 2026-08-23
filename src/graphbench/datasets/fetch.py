"""Download raw dataset files into data/raw and verify them.

Why verify at all, when it's a public file over HTTPS? Because a benchmark is a
claim about a specific graph. If SNAP re-publishes ca-AstroPh with an extra
1,000 edges next year, every number in results/ silently becomes
incomparable, and nothing would tell us. The checksum turns that into a loud
failure instead of a quiet one.

Why urllib and not requests: this is the only place in the harness that talks
HTTP, and one stdlib call is not worth a dependency that then has to be pinned
and audited.
"""

import hashlib
import shutil
import urllib.request
from pathlib import Path

from graphbench import paths
from graphbench.datasets.registry import Dataset

# 1 MiB. Big enough that syscall overhead disappears, small enough that hashing
# a 400 MB soc-Pokec download doesn't sit in memory.
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def local_path(ds: Dataset) -> Path:
    return paths.RAW_DIR / ds.url.rsplit("/", 1)[-1]


def fetch(ds: Dataset, force: bool = False) -> Path:
    """Return the local raw file, downloading it if we don't have it yet.

    Caching is deliberate: `make bench` is run many times while iterating on
    workloads, and re-pulling the source graph every time is both slow and rude
    to SNAP's servers. `--force` is the escape hatch.
    """
    dest = local_path(ds)
    paths.RAW_DIR.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        print(f"already have {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    else:
        print(f"downloading {ds.url}")
        # Download to .part first and rename at the end. Without this, a Ctrl-C
        # or a dropped connection leaves a truncated file that looks complete to
        # the cache check above, and the next run happily parses half a graph.
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(ds.url, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out, CHUNK)
        tmp.replace(dest)
        print(f"saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    digest = sha256_of(dest)
    if not ds.sha256:
        # First time this dataset has been fetched. Print the digest so it can be
        # pinned in the registry. Deliberately not auto-writing it back into the
        # source: that would mean "trust whatever the network gave us the first
        # time", which is not verification, it's just a record of one download.
        print(f"sha256 not pinned yet for {ds.name}: {digest}")
    elif digest != ds.sha256:
        # Hard fail rather than warn. A benchmark that ran on the wrong input is
        # worse than a benchmark that didn't run.
        raise RuntimeError(
            f"checksum mismatch for {dest.name}\n  expected {ds.sha256}\n  got      {digest}"
        )
    return dest
