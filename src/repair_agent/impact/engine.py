"""Cypher-first three-bucket impact analysis over FalkorDB evidence."""

from __future__ import annotations

from collections import defaultdict

from repair_agent.config import Settings, get_settings
from repair_agent.falkordb_io.client import FalkorDBIO, ImpactPath
from repair_agent.models import (
    DriftEvent,
    ImpactBucket,
    ImpactedAsset,
    ImpactReport,
    LineageEdge,
    LineageGraph,
    LineageHop,
    LineageNode,
)


class LineageUnavailable(RuntimeError):
    """Raised when the authoritative graph cannot establish an impact answer."""


class CodeMapMissing(RuntimeError):
    """Raised when a patch-required graph asset has no code mapping."""


class ImpactEngine:
    def __init__(self, falkordb_io: FalkorDBIO | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.io = falkordb_io or FalkorDBIO(self.settings)

    def analyze(self, drift: DriftEvent) -> ImpactReport:
        if not drift.old_column:
            raise ValueError("Impact analysis requires the pre-change column name.")
        paths = self.io.trace_column_impact(drift.dataset_urn, drift.old_column, max_hops=8)
        all_assets = self.io.list_code_assets()
        if not all_assets:
            raise LineageUnavailable("FalkorDB contains no code-bearing assets; run the idempotent ingester first.")

        by_asset: dict[str, list[ImpactPath]] = defaultdict(list)
        for path in paths:
            by_asset[path.asset_id].append(path)

        assets: list[ImpactedAsset] = []
        for asset in all_assets:
            candidates = by_asset.get(asset["id"], [])
            if not candidates:
                assets.append(
                    ImpactedAsset(
                        urn=asset["id"],
                        name=asset["name"],
                        kind=asset["kind"],
                        bucket=ImpactBucket.SKIPPED,
                        code_path=asset["code_path"],
                        reason=(
                            f"FalkorDB returned no FLOWS_TO path from `{drift.dataset_name}.{drift.old_column}` "
                            f"to any column written by `{asset['name']}`, and no direct READS edge from that asset."
                        ),
                    )
                )
                continue
            path = _best_path(candidates)
            insulated = path.operations != ["DIRECT_READ"] and any(path.alias_boundaries[:-1])
            bucket = ImpactBucket.DOWNSTREAM_UNAFFECTED if insulated else ImpactBucket.REQUIRES_PATCH
            assets.append(_impacted_asset(asset, path, bucket, drift))

        assets.sort(key=lambda item: (_bucket_order(item.bucket), item.hops or 999, item.name))
        graph = _render_graph(drift, assets)
        stats = {
            "requires_patch": sum(asset.bucket is ImpactBucket.REQUIRES_PATCH for asset in assets),
            "downstream_unaffected": sum(asset.bucket is ImpactBucket.DOWNSTREAM_UNAFFECTED for asset in assets),
            "skipped": sum(asset.bucket is ImpactBucket.SKIPPED for asset in assets),
            "total_scanned": len(assets),
            "max_hops_reached": max((asset.hops or 0 for asset in assets), default=0),
        }
        return ImpactReport(drift=drift, assets=assets, graph=graph, stats=stats)


def _best_path(paths: list[ImpactPath]) -> ImpactPath:
    # Prefer a path with no alias before the consuming asset, then the shortest path.
    return sorted(paths, key=lambda path: (any(path.alias_boundaries[:-1]), path.hops, path.column_ids))[0]


def _impacted_asset(asset: dict[str, object], path: ImpactPath, bucket: ImpactBucket, drift: DriftEvent) -> ImpactedAsset:
    evidence = " -> ".join(
        f"{dataset.removeprefix('dataset:')}.{column}"
        for dataset, column in zip(path.dataset_ids, path.column_names, strict=True)
    )
    lineage_path = [
        LineageHop(
            upstream_urn=path.dataset_ids[index],
            upstream_column=path.column_names[index],
            downstream_urn=path.dataset_ids[index + 1],
            downstream_column=path.column_names[index + 1],
            transform_operation=path.operations[index],
            hops=index + 1,
        )
        for index in range(max(0, len(path.column_names) - 1))
    ]
    if path.operations == ["DIRECT_READ"]:
        reason = (
            f"FalkorDB has a direct READS edge from `{path.name}` to "
            f"`{drift.dataset_name}.{drift.old_column}`; its mapped SQL must be patched."
        )
    elif bucket is ImpactBucket.REQUIRES_PATCH:
        reason = (
            f"FalkorDB path `{evidence}` reaches `{path.name}` without an earlier alias boundary. "
            "The asset reads the drifted name before producing its output, so it requires a patch."
        )
    else:
        alias_index = next(index for index, value in enumerate(path.alias_boundaries[:-1]) if value)
        reason = (
            f"FalkorDB path `{evidence}` crosses alias "
            f"`{path.column_names[alias_index]} -> {path.column_names[alias_index + 1]}` before `{path.name}`. "
            "The asset consumes the stable alias and is intentionally not patched."
        )
    return ImpactedAsset(
        urn=str(asset["id"]),
        name=str(asset["name"]),
        kind=str(asset["kind"]),
        bucket=bucket,
        hops=path.hops,
        matched_columns=list(dict.fromkeys(path.column_names)),
        code_path=str(asset["code_path"]) if asset["code_path"] else None,
        reason=reason,
        lineage_path=lineage_path,
    )


def _render_graph(drift: DriftEvent, assets: list[ImpactedAsset]) -> LineageGraph:
    nodes: dict[str, LineageNode] = {
        drift.dataset_urn: LineageNode(
            urn=drift.dataset_urn,
            name=drift.dataset_name,
            kind="dataset",
            columns=[drift.old_column] if drift.old_column else [],
            hops=0,
        )
    }
    edges: dict[tuple[str, str, str | None], LineageEdge] = {}
    for asset in assets:
        nodes[asset.urn] = LineageNode(
            urn=asset.urn,
            name=asset.name,
            kind=asset.kind,
            bucket=asset.bucket,
            columns=asset.matched_columns,
            hops=asset.hops,
        )
        for hop in asset.lineage_path:
            nodes.setdefault(
                hop.downstream_urn,
                LineageNode(
                    urn=hop.downstream_urn,
                    name=hop.downstream_urn.removeprefix("dataset:"),
                    kind="dataset",
                    columns=[hop.downstream_column] if hop.downstream_column else [],
                    hops=hop.hops,
                ),
            )
            key = (hop.upstream_urn, hop.downstream_urn, hop.transform_operation)
            edges[key] = LineageEdge(
                source_urn=hop.upstream_urn,
                target_urn=hop.downstream_urn,
                source_columns=[hop.upstream_column] if hop.upstream_column else [],
                target_columns=[hop.downstream_column] if hop.downstream_column else [],
                transform_operation=hop.transform_operation,
            )
    return LineageGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def _bucket_order(bucket: ImpactBucket) -> int:
    return {
        ImpactBucket.REQUIRES_PATCH: 0,
        ImpactBucket.DOWNSTREAM_UNAFFECTED: 1,
        ImpactBucket.SKIPPED: 2,
    }[bucket]


def analyze(
    drift: DriftEvent,
    falkordb_io: FalkorDBIO | None = None,
    settings: Settings | None = None,
) -> ImpactReport:
    return ImpactEngine(falkordb_io, settings).analyze(drift)
