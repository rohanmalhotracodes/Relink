"""Surgical source-edit and deterministic drift-inference regressions."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import sqlglot

from repair_agent.codegen.airflow_ops import rename_column as rename_airflow_column
from repair_agent.codegen.jinja import mask, unmask
from repair_agent.codegen.sql_ops import find_column_references, rename_column
from repair_agent.drift.detect import declare_drift, detect_drift
from repair_agent.drift.snapshot import SchemaSnapshot
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.models import ColumnSpec, DriftKind

REPO_ROOT = Path(__file__).resolve().parents[1]
ORDERS = dataset_id("shop_prod.raw.orders")


def _column(name: str, native_type: str = "TIMESTAMP_NTZ") -> ColumnSpec:
    return ColumnSpec(name=name, native_type=native_type, data_type="time")


def _snapshot(columns: list[ColumnSpec]) -> SchemaSnapshot:
    return SchemaSnapshot({ORDERS: {column.name: column for column in columns}})


def test_exact_reference_rename_avoids_prefix_false_positive() -> None:
    sql = "select order_placed_at_utc, order_placed_at from shop_prod.raw.orders\n"
    matches = [reference for reference in find_column_references(sql) if reference.name == "order_placed_at"]
    assert len(matches) == 1
    assert rename_column(sql, "order_placed_at", "order_created_at") == (
        "select order_placed_at_utc, order_created_at from shop_prod.raw.orders\n"
    )


def test_jinja_round_trip_and_parse_for_every_demo_model() -> None:
    tags = re.compile(r"\{\{.*?\}\}", re.DOTALL)
    for path in sorted((REPO_ROOT / "demo-warehouse" / "models").glob("**/*.sql")):
        source = path.read_text(encoding="utf-8")
        masked, mapping = mask(source)
        assert unmask(masked, mapping) == source
        generated = sqlglot.parse_one(masked, dialect="snowflake").sql(dialect="snowflake", pretty=True)
        restored = unmask(generated, mapping)
        assert tags.findall(restored) == tags.findall(source), path
        sqlglot.parse_one(mask(restored)[0], dialect="snowflake")


def test_stg_orders_rename_changes_only_target_line() -> None:
    path = REPO_ROOT / "demo-warehouse" / "models" / "staging" / "stg_orders.sql"
    before = path.read_text(encoding="utf-8")
    after = rename_column(before, "order_placed_at", "order_created_at")
    changed = [(old, new) for old, new in zip(before.splitlines(), after.splitlines(), strict=True) if old != new]
    assert changed == [("    order_placed_at,", "    order_created_at,")]


def test_airflow_rewrite_preserves_python_and_triple_quotes() -> None:
    path = REPO_ROOT / "demo-warehouse" / "dags" / "shopflow_daily.py"
    before = path.read_text(encoding="utf-8")
    after = rename_airflow_column(before, ["RECENT_ORDERS_SQL"], "order_placed_at", "order_created_at")
    ast.parse(after)
    assert 'RECENT_ORDERS_SQL = """' in after
    assert "PRODUCT_CATALOG_SQL" in after
    assert after.count("order_created_at") == 2


def test_clean_rename_uses_same_type_fast_path() -> None:
    events = detect_drift(
        _snapshot([_column("id", "NUMBER"), _column("order_placed_at")]),
        _snapshot([_column("id", "NUMBER"), _column("order_created_at")]),
    )
    assert len(events) == 1
    assert events[0].kind is DriftKind.RENAME
    assert events[0].confidence == 0.95
    assert "same ordinal position (2)" in events[0].rationale
    assert events[0].id == "rename-orders-order_placed_at"


def test_rename_plus_unrelated_add_pairs_deterministically() -> None:
    events = detect_drift(
        _snapshot([_column("id", "NUMBER"), _column("placed_at")]),
        _snapshot([_column("id", "NUMBER"), _column("created_at"), _column("new_flag", "BOOLEAN")]),
    )
    assert [(event.kind, event.old_column, event.new_column) for event in events] == [
        (DriftKind.ADD, None, "new_flag"),
        (DriftKind.RENAME, "placed_at", "created_at"),
    ]


def test_retype_drop_no_drift_and_explicit_declaration() -> None:
    retype = detect_drift(
        _snapshot([_column("gross_amount", "NUMBER(12,2)")]),
        _snapshot([_column("gross_amount", "VARCHAR(20)")]),
    )
    assert len(retype) == 1 and retype[0].kind is DriftKind.RETYPE
    dropped = detect_drift(_snapshot([_column("marketing_opt_in", "BOOLEAN")]), _snapshot([]))
    assert len(dropped) == 1 and dropped[0].kind is DriftKind.DROP
    clean = _snapshot([_column("id", "NUMBER"), _column("placed_at")])
    assert detect_drift(clean, clean) == []
    explicit = declare_drift(kind="DROP", dataset_urn=ORDERS, old_column="legacy", old_type="VARCHAR")
    assert explicit.id == "drop-orders-legacy" and "explicit declaration" in explicit.rationale
