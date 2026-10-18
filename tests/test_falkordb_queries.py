from __future__ import annotations

from typing import Any

from repair_agent.config import Settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ids import dataset_id
from tests.support import Result


class RecordingGraph:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def ro_query(self, query: str, params: dict[str, Any] | None = None) -> Result:
        self.queries.append((query, params or {}))
        if "CALL algo.bfs" in query:
            return Result(
                [
                    [
                        ["column:downstream:a", "column:downstream:b"],
                        ["IDENTITY", "CAST"],
                        ["dataset:shop_prod.analytics.fct_orders"],
                    ]
                ]
            )
        if "FLOWS_TO*1..8" in query:
            return Result(
                [
                    [
                        dataset_id("shop_prod.analytics.mart_daily_revenue"),
                        "mart_daily_revenue",
                        "dbt_model",
                        "demo-warehouse/models/marts/mart_daily_revenue.sql",
                        3,
                        ["c1", "c2", "c3", "c4"],
                        [
                            dataset_id("shop_prod.raw.orders"),
                            dataset_id("shop_prod.analytics.stg_orders"),
                            dataset_id("shop_prod.analytics.fct_orders"),
                            dataset_id("shop_prod.analytics.mart_daily_revenue"),
                        ],
                        ["order_placed_at", "order_placed_at", "order_date", "order_date"],
                        ["IDENTITY", "CAST", "IDENTITY"],
                        [False, True, False],
                    ]
                ]
            )
        return Result([])

    query = ro_query


def test_multi_hop_impact_is_a_falkordb_cypher_query() -> None:
    graph = RecordingGraph()
    io = FalkorDBIO(Settings(_env_file=None), graph=graph)
    paths = io.trace_column_impact(dataset_id("shop_prod.raw.orders"), "order_placed_at", max_hops=8)
    assert paths[0].hops == 3
    assert paths[0].alias_boundaries == [False, True, False]
    cypher, params = graph.queries[0]
    assert "FLOWS_TO*1..8" in cypher
    assert "relationships(p)" in cypher
    assert params["source_id"] == "column:shop_prod.raw.orders:order_placed_at"


def test_bfs_agent_tool_uses_falkordb_native_algorithm() -> None:
    graph = RecordingGraph()
    io = FalkorDBIO(Settings(_env_file=None), graph=graph)
    result = io.explore_lineage_bfs(dataset_id("shop_prod.raw.orders"), "order_placed_at", max_depth=8)
    assert result["algorithm"] == "algo.bfs"
    assert result["asset_ids"] == ["dataset:shop_prod.analytics.fct_orders"]
    cypher, params = graph.queries[0]
    assert "CALL algo.bfs(source, $max_depth, 'FLOWS_TO')" in cypher
    assert "YIELD nodes, edges" in cypher
    assert params["max_depth"] == 8
