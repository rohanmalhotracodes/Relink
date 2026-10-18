#!/usr/bin/env python3
"""Idempotently ingest and optionally verify the ShopFlow FalkorDB graph."""

from __future__ import annotations

import argparse

from rich.console import Console

from repair_agent.config import get_settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ingest import ingest, load_seed, verify

CONSOLE = Console()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Delete only the configured graph before ingestion.")
    parser.add_argument("--verify", action="store_true", help="Read back node and edge counts.")
    parser.add_argument("--dry-run", action="store_true", help="Parse inputs and report counts without graph writes.")
    args = parser.parse_args()
    settings = get_settings()
    if args.dry_run:
        counts = {key: len(value) for key, value in load_seed(settings).items()}
        CONSOLE.print(f"[yellow]Dry run:[/] {counts}")
        return 0
    io = FalkorDBIO(settings)
    info = io.preflight()
    CONSOLE.print(f"[green]FalkorDB reachable:[/] {info['host']}:{info['port']} / {info['graph']}")
    counts = ingest(io, settings, reset=args.reset)
    CONSOLE.print(f"[green]Ingestion complete:[/] {counts}")
    if args.verify:
        CONSOLE.print(f"[bold green]Verification passed:[/] {verify(io, settings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
