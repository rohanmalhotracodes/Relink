from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repair_agent.config import Settings
from repair_agent.falkordb_io.client import ImpactPath
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.falkordb_io.ingest import load_seed
from repair_agent.models import ColumnSpec, DatasetSchema


class OfflineFalkorIO:
    """Test double at the database-driver boundary, populated from ingestion inputs."""

    def __init__(self) -> None:
        payload = load_seed(Settings(_env_file=None))
        self.schemas: dict[str, list[ColumnSpec]] = {}
        for row in payload["columns"]:
            self.schemas.setdefault(row["dataset_id"], []).append(
                ColumnSpec(
                    name=row["name"],
                    native_type=row["native_type"],
                    data_type=row["data_type"],
                    description=row["description"],
                    nullable=row["nullable"],
                )
            )
        raw = dataset_id("shop_prod.raw.orders")
        for index, column in enumerate(self.schemas[raw]):
            if column.name == "order_placed_at":
                self.schemas[raw][index] = column.model_copy(update={"name": "order_created_at"})
        self.recorded: list[dict[str, Any]] = []

    def preflight(self) -> dict[str, object]:
        return {"graph": "offline"}

    def get_schema(self, identifier: str, *, skip_cache: bool = True) -> DatasetSchema:
        del skip_cache
        return DatasetSchema(dataset_urn=identifier, columns=self.schemas.get(identifier, []))

    def list_namespace_datasets(self, prefix: str, *, skip_cache: bool = True) -> list[str]:
        del skip_cache
        return sorted(identifier for identifier in self.schemas if identifier.removeprefix("dataset:").startswith(prefix))

    def trace_column_impact(self, source: str, column: str, *, max_hops: int = 8) -> list[ImpactPath]:
        del max_hops
        assert source == dataset_id("shop_prod.raw.orders")
        assert column == "order_placed_at"
        raw = dataset_id("shop_prod.raw.orders")
        stg = dataset_id("shop_prod.analytics.stg_orders")
        fct = dataset_id("shop_prod.analytics.fct_orders")
        daily = dataset_id("shop_prod.analytics.mart_daily_revenue")
        ltv = dataset_id("shop_prod.analytics.mart_customer_ltv")
        return [
            _path(stg, "stg_orders", [raw, stg], ["order_placed_at", "order_placed_at"], ["IDENTITY"], [False]),
            _path(
                fct,
                "fct_orders",
                [raw, stg, fct],
                ["order_placed_at", "order_placed_at", "order_date"],
                ["IDENTITY", "CAST"],
                [False, True],
            ),
            _path(
                daily,
                "mart_daily_revenue",
                [raw, stg, fct, daily],
                ["order_placed_at", "order_placed_at", "order_date", "order_date"],
                ["IDENTITY", "CAST", "IDENTITY"],
                [False, True, False],
            ),
            _path(
                ltv,
                "mart_customer_ltv",
                [raw, stg, fct, ltv],
                ["order_placed_at", "order_placed_at", "order_date", "first_order_date"],
                ["IDENTITY", "CAST", "MIN"],
                [False, True, True],
            ),
            ImpactPath(
                asset_id="job:shopflow_daily.extract_recent_orders",
                name="extract_recent_orders",
                kind="airflow_task",
                code_path="demo-warehouse/dags/shopflow_daily.py",
                hops=1,
                column_ids=["column:shop_prod.raw.orders:order_placed_at"],
                dataset_ids=[raw],
                column_names=["order_placed_at"],
                operations=["DIRECT_READ"],
                alias_boundaries=[False],
            ),
        ]

    def explore_lineage_bfs(self, source: str, column: str, *, max_depth: int = 8) -> dict[str, Any]:
        paths = self.trace_column_impact(source, column, max_hops=max_depth)
        return {
            "algorithm": "algo.bfs",
            "source_column_id": f"column:{source.removeprefix('dataset:')}:{column}",
            "max_depth": max_depth,
            "column_ids": sorted({identifier for path in paths for identifier in path.column_ids[1:]}),
            "operations": sorted({operation for path in paths for operation in path.operations}),
            "asset_ids": sorted({path.asset_id for path in paths}),
        }

    def list_code_assets(self) -> list[dict[str, Any]]:
        payload = load_seed(Settings(_env_file=None))
        assets = [
            {
                "id": model["produces"],
                "name": model["name"],
                "kind": "dbt_model",
                "code_path": next(
                    file["path"]
                    for file in payload["files"]
                    if file["kind"] == "sql" and file["path"].endswith(f"/{model['name']}.sql")
                ),
            }
            for model in payload["models"]
        ]
        assets.append(
            {
                "id": "job:shopflow_daily.extract_recent_orders",
                "name": "extract_recent_orders",
                "kind": "airflow_task",
                "code_path": "demo-warehouse/dags/shopflow_daily.py",
            }
        )
        return assets

    def validate_column(self, dataset: str, column: str) -> bool:
        return any(item.name.casefold() == column.casefold() for item in self.schemas.get(dataset, []))

    def record_incident(self, **values: Any) -> None:
        self.recorded.append({"kind": "incident", **values})

    def record_repair_run(self, **values: Any) -> None:
        self.recorded.append({"kind": "run", **values})


def _path(
    asset_id: str,
    name: str,
    datasets: list[str],
    columns: list[str],
    operations: list[str],
    aliases: list[bool],
) -> ImpactPath:
    return ImpactPath(
        asset_id=asset_id,
        name=name,
        kind="dbt_model",
        code_path=f"demo-warehouse/models/{name}.sql",
        hops=len(operations),
        column_ids=[
            f"column:{dataset.removeprefix('dataset:')}:{column}" for dataset, column in zip(datasets, columns, strict=True)
        ],
        dataset_ids=datasets,
        column_names=columns,
        operations=operations,
        alias_boundaries=aliases,
    )


@dataclass
class Result:
    result_set: list[list[Any]]
