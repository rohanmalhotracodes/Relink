#!/usr/bin/env python3
"""Apply or revert one demo schema drift directly in FalkorDB."""

from __future__ import annotations

import argparse
import json

from repair_agent.config import get_settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.falkordb_io.ingest import ingest

SCENARIOS = {
    "rename_order_placed_at": {
        "dataset": "shop_prod.raw.orders",
        "old_column": "order_placed_at",
        "new_column": "order_created_at",
    },
    "retype_gross_amount": {
        "dataset": "shop_prod.raw.orders",
        "old_column": "gross_amount",
        "new_type": "VARCHAR(20)",
    },
    "drop_marketing_opt_in": {
        "dataset": "shop_prod.raw.customers",
        "old_column": "marketing_opt_in",
        "drop": True,
    },
    "rename_jaffle_user_id": {
        "dataset": "jaffle_shop.raw.raw_orders",
        "old_column": "user_id",
        "new_column": "customer_user_id",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    parser.add_argument("--revert", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    io = FalkorDBIO(settings)
    io.preflight()
    state = settings.warehouse_path / ".relink" / "applied_drift.json"
    if args.revert:
        ingest(io, settings, reset=True)
        if state.exists():
            state.unlink()
        print("Restored the baseline graph.")
        return 0
    if args.scenario is None:
        parser.error("scenario is required unless --revert is used")
    if state.exists():
        raise RuntimeError("A drift is already active; revert or reset before applying another scenario.")
    scenario = SCENARIOS[args.scenario]
    io.apply_schema_change(dataset_id(scenario["dataset"]), **{k: v for k, v in scenario.items() if k != "dataset"})
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"scenario": args.scenario}, indent=2) + "\n", encoding="utf-8")
    print(f"Applied {args.scenario} to FalkorDB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
