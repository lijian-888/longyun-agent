"""Dedicated local worker for long-running genotype conversion and QC jobs."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .genotype_assets import worker_loop
from .object_storage import object_storage_manager
from .tenancy import tenant_database_manager


def _workspace(root: Path, institution_id: str) -> Path:
    path = root / "workspaces" / institution_id / "research"
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    root = Path(os.getenv("RESEARCH_STORAGE_DIR", "/data/research"))
    tenant_database_manager.ensure_control_schema()
    bindings = tenant_database_manager.active_bindings()
    if not bindings:
        raise RuntimeError("No active institution is registered for the genotype worker.")

    threads: list[threading.Thread] = []
    for binding in bindings:
        tenant_database_manager.verify_tenant_database(binding.institution_id)
        thread = threading.Thread(
            name=f"genotype-{binding.institution_id}",
            target=worker_loop,
            args=(
                binding.migration_database_url,
                _workspace(root, binding.institution_id),
                object_storage_manager.for_institution(binding.institution_id),
            ),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    # A dead tenant worker must fail the container so Compose can restart it;
    # silently serving only one institution would be operationally unsafe.
    while True:
        dead = [thread.name for thread in threads if not thread.is_alive()]
        if dead:
            raise RuntimeError(f"Genotype worker thread stopped: {', '.join(dead)}")
        time.sleep(5)


if __name__ == "__main__":
    main()
