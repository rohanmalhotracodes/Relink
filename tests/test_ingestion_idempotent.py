from __future__ import annotations

from typing import Any

from repair_agent.config import Settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ingest import ingest
from tests.support import Result


class MergeGraph:
    def __init__(self) -> None:
        self.nodes: set[tuple[str, str]] = set()
        self.relationships: set[tuple[str, str, str]] = set()

    def query(self, query: str, params: dict[str, Any] | None = None) -> Result:
        rows = (params or {}).get("rows", [])
        if "MERGE (d:Dataset" in query:
            self.nodes.update(("Dataset", row["id"]) for row in rows)
        elif "MERGE (c:Column" in query:
            self.nodes.update(("Column", row["id"]) for row in rows)
            self.relationships.update((row["dataset_id"], "HAS_COLUMN", row["id"]) for row in rows)
        elif "MERGE (m:DbtModel" in query:
            self.nodes.update(("DbtModel", row["id"]) for row in rows)
            self.relationships.update((row["id"], "PRODUCES", row["produces"]) for row in rows)
        elif "MERGE (j:AirflowJob" in query:
            self.nodes.update(("AirflowJob", row["id"]) for row in rows)
        elif "MERGE (f:CodeAsset" in query:
            self.nodes.update(("CodeAsset", row["id"]) for row in rows)
        elif "IMPLEMENTS" in query:
            self.relationships.update((row["file_id"], "IMPLEMENTS", row["asset_id"]) for row in rows)
        elif "[:READS]" in query:
            self.relationships.update((row["asset_id"], "READS", row["column_id"]) for row in rows)
        elif "[:WRITES]" in query:
            self.relationships.update((row["asset_id"], "WRITES", row["column_id"]) for row in rows)
        elif "[:DEPENDS_ON]" in query:
            self.relationships.update((row["asset_id"], "DEPENDS_ON", row["upstream_id"]) for row in rows)
        elif "FLOWS_TO" in query:
            self.relationships.update((row["source_id"], "FLOWS_TO", row["target_id"]) for row in rows)
        return Result([])

    ro_query = query


def test_ingestion_can_repeat_without_duplicate_nodes_or_edges(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("repair_agent.falkordb_io.ingest.write_snapshot", lambda *_args: None)
    graph = MergeGraph()
    settings = Settings(_env_file=None)
    io = FalkorDBIO(settings, graph=graph)
    first = ingest(io, settings)
    first_counts = (len(graph.nodes), len(graph.relationships))
    second = ingest(io, settings)
    assert first == second
    assert (len(graph.nodes), len(graph.relationships)) == first_counts
    assert first_counts[0] > 0 and first_counts[1] > 0
