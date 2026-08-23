"""Download raw dataset files into data/raw and verify them.

Checksums matter because a benchmark is a claim about a specific graph: if SNAP
republishes ca-AstroPh with more edges, every number in results/ silently becomes
incomparable. The checksum turns that into a loud failure.
"""

import hashlib
import shutil
import urllib.request
from pathlib import Path

from graphbench import paths
from graphbench.datasets.registry import Dataset

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
    dest = local_path(ds)
    paths.RAW_DIR.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        print(f"already have {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    else:
        print(f"downloading {ds.url}")
        # .part then rename: a Ctrl-C mid-download otherwise leaves a truncated
        # file that the cache check above treats as complete.
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(ds.url, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out, CHUNK)
        tmp.replace(dest)
        print(f"saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    digest = sha256_of(dest)
    if not ds.sha256:
        # Print it for pinning. Not written back automatically, since trusting the
        # first download is a record of one download, not verification.
        print(f"sha256 not pinned yet for {ds.name}: {digest}")
    elif digest != ds.sha256:
        raise RuntimeError(
            f"checksum mismatch for {dest.name}\n  expected {ds.sha256}\n  got      {digest}"
        )
    return dest
