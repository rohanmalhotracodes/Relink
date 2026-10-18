"""Stdio MCP server exposing concise FalkorDB reasoning tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.tools import FalkorDBTools

mcp = FastMCP("falkordb-schema-repair")


def _tools() -> FalkorDBTools:
    return FalkorDBTools(FalkorDBIO())


@mcp.tool()
def inspect_schema(dataset_id: str) -> dict:
    """Return FalkorDB's active schema for a stable dataset ID."""
    return _tools().inspect_schema(dataset_id)


@mcp.tool()
def trace_column_impact(dataset_id: str, column: str, max_hops: int = 8) -> dict:
    """Traverse multi-hop downstream column lineage and return graph paths."""
    return _tools().trace_column_impact(dataset_id, column, max_hops)


@mcp.tool()
def explore_lineage_bfs(dataset_id: str, column: str, max_depth: int = 8) -> dict:
    """Run FalkorDB's native algo.bfs procedure over downstream column lineage."""
    return _tools().explore_lineage_bfs(dataset_id, column, max_depth)


@mcp.tool()
def explain_lineage_path(dataset_id: str, column: str, asset_id: str) -> dict:
    """Explain the exact path from a source column to one code-bearing asset."""
    return _tools().explain_lineage_path(dataset_id, column, asset_id)


@mcp.tool()
def classify_affected_asset(dataset_id: str, column: str, asset_id: str) -> dict:
    """Classify an asset using graph path and alias-boundary evidence."""
    return _tools().classify_affected_asset(dataset_id, column, asset_id)


@mcp.tool()
def assess_repair_candidates(dataset_id: str, column: str) -> dict:
    """Classify every code-bearing asset using FalkorDB path evidence."""
    return _tools().assess_repair_candidates(dataset_id, column)


@mcp.tool()
def validate_candidate_repair(references: list[dict[str, str]]) -> dict:
    """Validate candidate dataset/column references against FalkorDB."""
    return _tools().validate_candidate_repair(references)


@mcp.tool()
def record_repair_result(
    run_id: str,
    incident_id: str,
    status: str,
    pr_url: str,
    patched_asset_ids: list[str],
) -> dict:
    """Record a completed repair and its patched assets in FalkorDB."""
    return _tools().record_repair_result(run_id, incident_id, status, pr_url, patched_asset_ids)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
