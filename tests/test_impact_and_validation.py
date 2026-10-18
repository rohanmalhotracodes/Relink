from __future__ import annotations

from repair_agent.drift.detect import declare_drift
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.impact.engine import analyze
from repair_agent.models import ImpactBucket, Patch
from repair_agent.validate.validator import validate_patch
from tests.support import OfflineFalkorIO


def _drift():
    return declare_drift(
        kind="RENAME",
        dataset_urn=dataset_id("shop_prod.raw.orders"),
        old_column="order_placed_at",
        new_column="order_created_at",
        old_type="TIMESTAMP_NTZ",
        new_type="TIMESTAMP_NTZ",
    )


def test_direct_alias_insulated_and_unrelated_assets_have_graph_reasons() -> None:
    report = analyze(_drift(), OfflineFalkorIO())  # type: ignore[arg-type]
    buckets = {bucket: {asset.name for asset in report.assets if asset.bucket is bucket} for bucket in ImpactBucket}
    assert buckets[ImpactBucket.REQUIRES_PATCH] == {"stg_orders", "fct_orders", "extract_recent_orders"}
    assert buckets[ImpactBucket.DOWNSTREAM_UNAFFECTED] == {"mart_daily_revenue", "mart_customer_ltv"}
    assert "mart_product_performance" in buckets[ImpactBucket.SKIPPED]
    assert "alias" in next(asset.reason for asset in report.assets if asset.name == "mart_daily_revenue").lower()
    assert "no FLOWS_TO path" in next(asset.reason for asset in report.assets if asset.name == "mart_product_performance")
    fct = next(asset for asset in report.assets if asset.name == "fct_orders")
    assert len(fct.lineage_path) == 2


def test_invalid_column_reference_fails_the_graph_validation_gate() -> None:
    patch = Patch(
        asset_urn="job:test",
        file_path="corrupt.sql",
        before="select order_id from shop_prod.raw.orders\n",
        after="select invented_column from shop_prod.raw.orders\n",
        unified_diff="",
        kind="dbt_sql",
        valid=True,
        strategy="test",
    )
    checks = validate_patch(patch, OfflineFalkorIO())  # type: ignore[arg-type]
    assert next(check for check in checks if check.column == "invented_column").status == "UNKNOWN_COLUMN"
    assert patch.valid is False
