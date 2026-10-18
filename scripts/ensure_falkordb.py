#!/usr/bin/env python3
"""Wait for FalkorDB and seed an empty graph without overwriting runtime state."""

from __future__ import annotations

import time

from repair_agent.config import get_settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ingest import ingest, verify


def main() -> int:
    settings = get_settings()
    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            io = FalkorDBIO(settings)
            io.preflight()
            datasets = io.list_namespace_datasets(settings.namespace_prefix, skip_cache=True)
            if not datasets:
                counts = ingest(io, settings)
                print(f"Seeded empty FalkorDB graph {settings.falkordb_graph}: {counts}")
            else:
                print(f"Using existing FalkorDB graph {settings.falkordb_graph} with {len(datasets)} datasets.")
            verify(io, settings)
            return 0
        except Exception as exc:  # database startup is intentionally retried
            last_error = exc
            print(f"Waiting for FalkorDB ({attempt}/30): {exc}")
            time.sleep(2)
    raise RuntimeError(f"FalkorDB did not become ready: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
