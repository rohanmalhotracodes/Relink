"""Structured FalkorDB tools shared by MCP transport and deterministic safeguards."""

from __future__ import annotations

from typing import Any

from repair_agent.falkordb_io.client import FalkorDBIO


class FalkorDBTools:
    def __init__(self, io: FalkorDBIO) -> None:
        self.io = io

    def inspect_schema(self, dataset_id: str) -> dict[str, Any]:
        schema = self.io.get_schema(dataset_id, skip_cache=True)
        return {
            "dataset_id": dataset_id,
            "schema_version_source": "FalkorDB",
            "columns": [column.model_dump(mode="json") for column in schema.columns],
        }

    def trace_column_impact(self, dataset_id: str, column: str, max_hops: int = 8) -> dict[str, Any]:
        paths = self.io.trace_column_impact(dataset_id, column, max_hops=max_hops)
        return {
            "source": {"dataset_id": dataset_id, "column": column},
            "paths": [
                {
                    "asset_id": path.asset_id,
                    "asset": path.name,
                    "kind": path.kind,
                    "hops": path.hops,
                    "columns": path.column_ids,
                    "operations": path.operations,
                    "alias_boundaries": path.alias_boundaries,
                }
                for path in paths
            ],
        }

    def explore_lineage_bfs(self, dataset_id: str, column: str, max_depth: int = 8) -> dict[str, Any]:
        """Run FalkorDB's native breadth-first-search procedure over column lineage."""

        return self.io.explore_lineage_bfs(dataset_id, column, max_depth=max_depth)

    def explain_lineage_path(self, dataset_id: str, column: str, asset_id: str) -> dict[str, Any]:
        paths = [path for path in self.io.trace_column_impact(dataset_id, column, max_hops=8) if path.asset_id == asset_id]
        if not paths:
            return {"affected": False, "asset_id": asset_id, "evidence": "No FalkorDB path or direct READS edge."}
        path = sorted(paths, key=lambda value: value.hops)[0]
        return {
            "affected": True,
            "asset_id": asset_id,
            "column_path": path.column_ids,
            "operations": path.operations,
            "alias_boundaries": path.alias_boundaries,
        }

    def classify_affected_asset(self, dataset_id: str, column: str, asset_id: str) -> dict[str, Any]:
        evidence = self.explain_lineage_path(dataset_id, column, asset_id)
        if not evidence["affected"]:
            return {**evidence, "classification": "SKIPPED", "reason": "No changed-column path in FalkorDB."}
        aliases = evidence["alias_boundaries"]
        insulated = evidence["operations"] != ["DIRECT_READ"] and any(aliases[:-1])
        return {
            **evidence,
            "classification": "DOWNSTREAM_UNAFFECTED" if insulated else "REQUIRES_PATCH",
            "reason": "An upstream alias boundary insulates this asset."
            if insulated
            else "The asset reads the changed name.",
        }

    def assess_repair_candidates(self, dataset_id: str, column: str) -> dict[str, Any]:
        """Return an evidence-backed classification for every code-bearing asset."""

        assets = self.io.list_code_assets()
        decisions = [
            {
                "asset_id": asset["id"],
                "asset": asset["name"],
                **self.classify_affected_asset(dataset_id, column, asset["id"]),
            }
            for asset in assets
        ]
        return {"source": {"dataset_id": dataset_id, "column": column}, "decisions": decisions}

    def validate_candidate_repair(self, references: list[dict[str, str]]) -> dict[str, Any]:
        results = [
            {
                **reference,
                "valid": self.io.validate_column(reference["dataset_id"], reference["column"]),
            }
            for reference in references
        ]
        return {"valid": all(result["valid"] for result in results), "references": results}

    def record_repair_result(
        self,
        run_id: str,
        incident_id: str,
        status: str,
        pr_url: str,
        patched_asset_ids: list[str],
    ) -> dict[str, Any]:
        self.io.record_repair_run(
            run_id=run_id,
            incident_id=incident_id,
            status=status,
            pr_url=pr_url,
            patched_asset_ids=patched_asset_ids,
        )
        return {"recorded": True, "run_id": run_id, "status": status, "patched_asset_ids": patched_asset_ids}
