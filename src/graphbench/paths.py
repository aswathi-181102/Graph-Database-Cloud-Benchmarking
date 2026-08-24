"""Repo-relative paths, so it doesn't matter where you run the CLI from."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PREPARED_DIR = DATA_DIR / "prepared"
RESULTS_DIR = ROOT / "results"
DOCS_DIR = ROOT / "docs"
CHARTS_DIR = DOCS_DIR / "charts"

PLATFORMS_FILE = CONFIG_DIR / "platforms.yaml"
WORKLOADS_FILE = CONFIG_DIR / "workloads.yaml"


def display(path: Path) -> str:
    """Repo-relative if it is inside the repo, absolute otherwise.

    relative_to() raises on anything outside ROOT, which is how a results dir
    pointed somewhere else turned into a ValueError at the end of an otherwise
    successful run.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def ensure_dirs() -> None:
    for d in (RAW_DIR, PREPARED_DIR, RESULTS_DIR, CHARTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
