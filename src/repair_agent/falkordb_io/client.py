"""Typed FalkorDB query boundary used by every runtime reasoning decision."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from repair_agent.config import Settings, get_settings
from repair_agent.falkordb_io.ids import column_id, dataset_name
from repair_agent.models import ColumnSpec, DatasetSchema


class GraphProtocol(Protocol):
    def query(self, query: str, params: dict[str, Any] | None = None) -> Any: ...
    def ro_query(self, query: str, params: dict[str, Any] | None = None) -> Any: ...


class FalkorDBPreflightError(RuntimeError):
    """Raised when the configured FalkorDB graph cannot answer a read query."""


@dataclass(frozen=True)
class ImpactPath:
    asset_id: str
    name: str
    kind: str
    code_path: str | None
    hops: int
    column_ids: list[str]
    dataset_ids: list[str]
    column_names: list[str]
    operations: list[str]
    alias_boundaries: list[bool]


class FalkorDBIO:
    """The sole runtime catalog and lineage gateway.

    Tests may inject a graph object that implements the official client's ``query`` and
    ``ro_query`` methods. Production always creates the official FalkorDB client.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        graph: GraphProtocol | None = None,
        on_query: Callable[[str, dict[str, Any], list[list[Any]]], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._on_query = on_query
        if graph is None:
            from falkordb import FalkorDB

            kwargs: dict[str, Any] = {
                "host": self.settings.falkordb_host,
                "port": self.settings.falkordb_port,
                "ssl": self.settings.falkordb_ssl,
            }
            if self.settings.falkordb_username:
                kwargs["username"] = self.settings.falkordb_username
            if self.settings.falkordb_password:
                kwargs["password"] = self.settings.falkordb_password
            graph = FalkorDB(**kwargs).select_graph(self.settings.falkordb_graph)
        self.graph = graph

    def _run(self, cypher: str, params: dict[str, Any] | None = None, *, readonly: bool = True) -> list[list[Any]]:
        values = params or {}
        result = self.graph.ro_query(cypher, values) if readonly else self.graph.query(cypher, values)
        rows = list(result.result_set)
        if self._on_query is not None:
            self._on_query(cypher, values, rows)
        return rows

    def preflight(self) -> dict[str, object]:
        try:
            # FalkorDB rejects GRAPH.RO_QUERY for a graph key that has not been created
            # yet. The regular query channel safely initializes the named graph while
            # this read-only Cypher expression still performs no data mutation.
            rows = self._run("RETURN 1 AS ok", readonly=False)
        except Exception as exc:
            endpoint = f"{self.settings.falkordb_host}:{self.settings.falkordb_port}"
            raise FalkorDBPreflightError(
                f"FalkorDB preflight failed at {endpoint} for graph {self.settings.falkordb_graph!r}: {exc}"
            ) from exc
        if not rows or rows[0][0] != 1:
            raise FalkorDBPreflightError("FalkorDB returned an unexpected preflight result.")
        return {
            "host": self.settings.falkordb_host,
            "port": self.settings.falkordb_port,
            "graph": self.settings.falkordb_graph,
        }

    def get_schema(self, dataset_id: str, *, skip_cache: bool = True) -> DatasetSchema:
        del skip_cache
        rows = self._run(
            """
            MATCH (d:Dataset {id: $dataset_id})-[:HAS_COLUMN]->(c:Column)
            WHERE c.active = true
            RETURN c.name, c.native_type, c.data_type, c.description, c.nullable
            ORDER BY c.ordinal
            """,
            {"dataset_id": dataset_id},
        )
        return DatasetSchema(
            dataset_urn=dataset_id,
            columns=[
                ColumnSpec(
                    name=row[0],
                    native_type=row[1],
                    data_type=row[2],
                    description=row[3],
                    nullable=bool(row[4]),
                )
                for row in rows
            ],
        )

    def list_namespace_datasets(self, prefix: str, *, skip_cache: bool = True) -> list[str]:
        del skip_cache
        rows = self._run(
            "MATCH (d:Dataset) WHERE d.name STARTS WITH $prefix RETURN d.id ORDER BY d.id",
            {"prefix": prefix},
        )
        return [row[0] for row in rows]

    def trace_column_impact(self, dataset_id: str, column: str, *, max_hops: int = 8) -> list[ImpactPath]:
        if not 1 <= max_hops <= 32:
            raise ValueError("max_hops must be between 1 and 32")
        source_id = column_id(dataset_name(dataset_id), column)
        rows = self._run(
            f"""
            MATCH p=(source:Column {{id: $source_id}})-[:FLOWS_TO*1..{max_hops}]->(output:Column)
            MATCH (asset)-[:WRITES]->(output)
            WHERE asset:DbtModel OR asset:AirflowJob
            OPTIONAL MATCH (asset)-[:PRODUCES]->(produced:Dataset)
            OPTIONAL MATCH (code:CodeAsset)-[:IMPLEMENTS]->(asset)
            WHERE code.kind IN ['sql', 'airflow_dag']
            RETURN coalesce(produced.id, asset.id), asset.name,
                   CASE WHEN asset:DbtModel THEN 'dbt_model' ELSE 'airflow_task' END,
                   code.path, length(p),
                   [n IN nodes(p) | n.id],
                   [n IN nodes(p) | n.dataset_id],
                   [n IN nodes(p) | n.name],
                   [r IN relationships(p) | r.operation],
                   [r IN relationships(p) | coalesce(r.alias_boundary, false)]
            ORDER BY length(p), asset.id
            """,
            {"source_id": source_id},
        )
        direct_jobs = self._run(
            """
            MATCH (asset:AirflowJob)-[:READS]->(source:Column {id: $source_id})
            OPTIONAL MATCH (code:CodeAsset)-[:IMPLEMENTS]->(asset)
            WHERE code.kind = 'airflow_dag'
            RETURN asset.id, asset.name, 'airflow_task', code.path
            ORDER BY asset.id
            """,
            {"source_id": source_id},
        )
        paths = [
            ImpactPath(
                asset_id=row[0],
                name=row[1],
                kind=row[2],
                code_path=row[3],
                hops=int(row[4]),
                column_ids=list(row[5]),
                dataset_ids=list(row[6]),
                column_names=list(row[7]),
                operations=list(row[8]),
                alias_boundaries=[bool(value) for value in row[9]],
            )
            for row in rows
        ]
        paths.extend(
            ImpactPath(
                asset_id=row[0],
                name=row[1],
                kind=row[2],
                code_path=row[3],
                hops=1,
                column_ids=[source_id],
                dataset_ids=[dataset_id],
                column_names=[column],
                operations=["DIRECT_READ"],
                alias_boundaries=[False],
            )
            for row in direct_jobs
        )
        return paths

    def explore_lineage_bfs(self, dataset_id: str, column: str, *, max_depth: int = 8) -> dict[str, Any]:
        """Explore downstream lineage with FalkorDB's native BFS algorithm.

        This deliberately uses ``CALL algo.bfs`` rather than reproducing breadth-first
        traversal in application memory. It is exposed to the agent as complementary
        discovery evidence; the detailed impact engine still reads relationship
        properties from explicit Cypher paths for alias-aware classification.
        """

        if not 1 <= max_depth <= 32:
            raise ValueError("max_depth must be between 1 and 32")
        source_id = column_id(dataset_name(dataset_id), column)
        rows = self._run(
            """
            MATCH (source:Column {id: $source_id})
            CALL algo.bfs(source, $max_depth, 'FLOWS_TO')
            YIELD nodes, edges
            WITH nodes, edges
            UNWIND nodes AS reached
            OPTIONAL MATCH (asset)-[:WRITES]->(reached)
            RETURN [node IN nodes | node.id],
                   [edge IN edges | edge.operation],
                   collect(DISTINCT asset.id)
            """,
            {"source_id": source_id, "max_depth": max_depth},
        )
        if not rows:
            return {
                "algorithm": "algo.bfs",
                "source_column_id": source_id,
                "max_depth": max_depth,
                "column_ids": [],
                "operations": [],
                "asset_ids": [],
            }
        return {
            "algorithm": "algo.bfs",
            "source_column_id": source_id,
            "max_depth": max_depth,
            "column_ids": list(rows[0][0]),
            "operations": list(rows[0][1]),
            "asset_ids": sorted(value for value in rows[0][2] if value),
        }

    def list_code_assets(self) -> list[dict[str, Any]]:
        rows = self._run(
            """
            MATCH (code:CodeAsset)-[:IMPLEMENTS]->(asset)
            WHERE asset:DbtModel OR asset:AirflowJob
            WITH code, asset
            WHERE code.kind IN ['sql', 'airflow_dag']
            OPTIONAL MATCH (asset)-[:PRODUCES]->(produced:Dataset)
            RETURN coalesce(produced.id, asset.id), asset.name,
                   CASE WHEN asset:DbtModel THEN 'dbt_model' ELSE 'airflow_task' END,
                   code.path
            ORDER BY asset.id
            """
        )
        return [{"id": row[0], "name": row[1], "kind": row[2], "code_path": row[3]} for row in rows]

    def validate_column(self, dataset_id: str, column: str) -> bool:
        rows = self._run(
            """
            MATCH (:Dataset {id: $dataset_id})-[:HAS_COLUMN]->(c:Column {name: $column})
            WHERE c.active = true
            RETURN count(c)
            """,
            {"dataset_id": dataset_id, "column": column},
        )
        return bool(rows and rows[0][0])

    def record_incident(self, *, incident_id: str, dataset_id: str, column: str, kind: str) -> None:
        self._run(
            """
            MERGE (i:Incident {id: $incident_id})
            SET i.kind=$kind, i.column=$column, i.status='OPEN', i.updated_at=$now
            WITH i MATCH (d:Dataset {id: $dataset_id})
            MERGE (i)-[:AFFECTS]->(d)
            """,
            {
                "incident_id": incident_id,
                "dataset_id": dataset_id,
                "column": column,
                "kind": kind,
                "now": datetime.now(UTC).isoformat(),
            },
            readonly=False,
        )

    def record_repair_run(
        self,
        *,
        run_id: str,
        incident_id: str,
        status: str,
        pr_url: str,
        patched_asset_ids: list[str],
    ) -> None:
        params = {
            "run_id": run_id,
            "incident_id": incident_id,
            "status": status,
            "pr_url": pr_url,
            "patched": patched_asset_ids,
            "now": datetime.now(UTC).isoformat(),
        }
        self._run(
            """
            MERGE (r:RepairRun {id: $run_id})
            SET r.status=$status, r.pr_url=$pr_url, r.finished_at=$now
            WITH r MATCH (i:Incident {id: $incident_id})
            MERGE (r)-[:HANDLES]->(i)
            SET i.status = CASE WHEN $status = 'succeeded' THEN 'RESOLVED' ELSE 'OPEN' END,
                i.updated_at=$now
            """,
            params,
            readonly=False,
        )
        self._run(
            """
            MATCH (r:RepairRun {id: $run_id})
            UNWIND $patched AS asset_id
            MATCH (asset {id: asset_id})
            MERGE (r)-[:PATCHED]->(asset)
            """,
            params,
            readonly=False,
        )

    def apply_schema_change(
        self,
        dataset_id: str,
        *,
        old_column: str,
        new_column: str | None = None,
        new_type: str | None = None,
        drop: bool = False,
    ) -> None:
        current = self.get_schema(dataset_id)
        target = next((column for column in current.columns if column.name == old_column), None)
        if target is None:
            raise ValueError(f"Active column {dataset_id}.{old_column} does not exist.")
        params = {
            "dataset_id": dataset_id,
            "old_id": column_id(dataset_name(dataset_id), old_column),
            "new_id": column_id(dataset_name(dataset_id), new_column or old_column),
            "old_column": old_column,
            "new_column": new_column or old_column,
            "native_type": new_type or target.native_type,
            "data_type": _logical_type(new_type or target.native_type),
        }
        if drop:
            self._run(
                "MATCH (c:Column {id:$old_id}) SET c.active=false, c.schema_version=c.schema_version+1",
                params,
                readonly=False,
            )
        elif new_column and new_column != old_column:
            self._run(
                """
                MATCH (d:Dataset {id:$dataset_id})-[:HAS_COLUMN]->(old:Column {id:$old_id})
                SET old.active=false
                MERGE (fresh:Column {id:$new_id})
                SET fresh.name=$new_column, fresh.dataset_id=$dataset_id,
                    fresh.native_type=$native_type, fresh.data_type=$data_type,
                    fresh.description=old.description, fresh.nullable=old.nullable,
                    fresh.ordinal=old.ordinal, fresh.active=true,
                    fresh.schema_version=old.schema_version+1
                MERGE (d)-[:HAS_COLUMN]->(fresh)
                """,
                params,
                readonly=False,
            )
        else:
            self._run(
                """
                MATCH (c:Column {id:$old_id})
                SET c.native_type=$native_type, c.data_type=$data_type,
                    c.schema_version=c.schema_version+1
                """,
                params,
                readonly=False,
            )
        self._run(
            "MATCH (d:Dataset {id:$dataset_id}) SET d.schema_version=d.schema_version+1",
            params,
            readonly=False,
        )


def _logical_type(native_type: str) -> str:
    upper = native_type.upper()
    if "BOOL" in upper:
        return "boolean"
    if "DATE" in upper and "TIME" not in upper:
        return "date"
    if "TIME" in upper:
        return "time"
    if any(token in upper for token in ("NUMBER", "DECIMAL", "INT", "FLOAT")):
        return "number"
    return "string"
