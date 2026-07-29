"""Dedicated local worker for long-running genotype conversion and QC jobs."""

from __future__ import annotations

import os
from pathlib import Path

from .genotype_assets import worker_loop


def main() -> None:
    database_url = os.getenv("MIGRATION_DATABASE_URL")
    if not database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required for the genotype worker.")
    worker_loop(database_url, Path(os.getenv("RESEARCH_STORAGE_DIR", "/data/research")))


if __name__ == "__main__":
    main()
