"""Repeatable ingestion of the demo warehouse, dbt, Airflow, and code map."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from sqlglot import parse_one

from repair_agent.codegen.jinja import mask
from repair_agent.config import Settings, get_settings
from repair_agent.falkordb_io.client import FalkorDBIO, _logical_type
from repair_agent.falkordb_io.ids import column_id, dataset_id, file_id, job_id, model_id


def load_seed(settings: Settings | None = None) -> dict[str, list[dict[str, Any]]]:
    config = settings or get_settings()
    warehouse = config.warehouse_path
    yaml = YAML(typ="safe")
    metadata = yaml.load(warehouse.joinpath("graph_metadata.yml"))
    code_map = yaml.load(warehouse.joinpath("code_map.yml"))
    descriptions = _descriptions(warehouse, yaml)

    datasets: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    for name, typed_columns in metadata["column_types"].items():
        kind = "source" if ".raw." in name else "dbt_model_output"
        datasets.append(
            {
                "id": dataset_id(name),
                "name": name,
                "kind": kind,
                "platform": "snowflake" if kind == "source" else "dbt",
                "description": descriptions.get(name, ""),
                "schema_version": int(metadata["schema_version"]),
                "namespace": name.rsplit(".", 1)[0] + ".",
            }
        )
        for ordinal, (column, native_type) in enumerate(typed_columns.items()):
            column_meta = descriptions.get(f"{name}.{column}", {})
            columns.append(
                {
                    "id": column_id(name, column),
                    "dataset_id": dataset_id(name),
                    "name": column,
                    "native_type": native_type,
                    "data_type": _logical_type(native_type),
                    "description": column_meta.get("description", "") if isinstance(column_meta, dict) else "",
                    "nullable": not bool(column_meta.get("not_null", False)) if isinstance(column_meta, dict) else True,
                    "ordinal": ordinal,
                    "active": True,
                    "schema_version": int(metadata["schema_version"]),
                }
            )

    models: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    implements: list[dict[str, str]] = []
    for produced_id, mapping in code_map.get("datasets", {}).items():
        name = mapping["model_name"]
        mid = model_id(name)
        models.append({"id": mid, "name": name, "produces": produced_id})
        mapped_paths = (
            (mapping["sql"], mapping.get("repo_path", mapping["sql"]), "sql"),
            (
                mapping["schema_yml"],
                mapping.get("schema_repo_path", mapping["schema_yml"]),
                "dbt_schema",
            ),
        )
        for local_path, repository_path, file_kind in mapped_paths:
            fid = file_id(repository_path)
            files.append(_code_asset(config.repo_root, local_path, file_kind, asset_path=repository_path))
            implements.append({"file_id": fid, "asset_id": mid})

    lineage: list[dict[str, Any]] = []
    model_reads: list[dict[str, str]] = []
    model_writes: list[dict[str, str]] = []
    dependencies: set[tuple[str, str]] = set()
    produced_to_model = {model["produces"]: model["id"] for model in models}
    for source_dataset, source_column, target_dataset, target_column, operation in metadata["column_lineage"]:
        upstream_id = dataset_id(source_dataset)
        downstream_id = dataset_id(target_dataset)
        source_column_id = column_id(source_dataset, source_column)
        target_column_id = column_id(target_dataset, target_column)
        lineage.append(
            {
                "source_id": source_column_id,
                "target_id": target_column_id,
                "operation": operation,
                "alias_boundary": source_column.casefold() != target_column.casefold(),
            }
        )
        target_model = produced_to_model.get(downstream_id)
        if target_model:
            model_reads.append({"asset_id": target_model, "column_id": source_column_id})
            model_writes.append({"asset_id": target_model, "column_id": target_column_id})
            upstream_model = produced_to_model.get(upstream_id)
            if upstream_model:
                dependencies.add((target_model, upstream_model))

    jobs: list[dict[str, Any]] = []
    job_reads: list[dict[str, str]] = []
    job_writes: list[dict[str, str]] = []
    columns_by_dataset: dict[str, list[str]] = {}
    for column in columns:
        columns_by_dataset.setdefault(column["dataset_id"], []).append(column["id"])
    airflow = metadata["airflow"]
    dag_id = airflow["dag_id"]
    declared_tasks = _airflow_tasks(warehouse / "dags" / "shopflow_daily.py")
    for task_name, task_meta in airflow["jobs"].items():
        if task_name not in declared_tasks:
            raise ValueError(f"Airflow metadata references missing task {task_name!r}")
        jid = job_id(dag_id, task_name)
        jobs.append({"id": jid, "name": task_name, "dag_id": dag_id})
        for source_dataset, source_column in (task_meta or {}).get("reads", []):
            job_reads.append({"asset_id": jid, "column_id": column_id(source_dataset, source_column)})
        for source_dataset in (task_meta or {}).get("reads_datasets", []):
            job_reads.extend(
                {"asset_id": jid, "column_id": cid} for cid in columns_by_dataset.get(dataset_id(source_dataset), [])
            )
        for target_dataset in (task_meta or {}).get("writes_datasets", []):
            job_writes.extend(
                {"asset_id": jid, "column_id": cid} for cid in columns_by_dataset.get(dataset_id(target_dataset), [])
            )
    for configured_id, mapping in code_map.get("datajobs", {}).items():
        if configured_id not in {job["id"] for job in jobs}:
            raise ValueError(f"code_map.yml references unknown Airflow job {configured_id}")
        path = mapping["file"]
        fid = file_id(path)
        files.append(_code_asset(config.repo_root, path, "airflow_dag"))
        implements.append({"file_id": fid, "asset_id": configured_id})

    job_dependencies = [
        {"asset_id": job_id(dag_id, downstream), "upstream_id": job_id(dag_id, upstream)}
        for upstream, downstream in airflow.get("dependencies", [])
    ]
    model_dependencies = [
        {"asset_id": downstream, "upstream_id": upstream} for downstream, upstream in sorted(dependencies)
    ]
    return {
        "datasets": _unique(datasets),
        "columns": _unique(columns),
        "models": _unique(models),
        "jobs": _unique(jobs),
        "files": _unique(files),
        "implements": _unique(implements, "file_id", "asset_id"),
        "lineage": _unique(lineage, "source_id", "target_id"),
        "reads": _unique([*model_reads, *job_reads], "asset_id", "column_id"),
        "writes": _unique([*model_writes, *job_writes], "asset_id", "column_id"),
        "dependencies": _unique([*model_dependencies, *job_dependencies], "asset_id", "upstream_id"),
    }


def ingest(io: FalkorDBIO, settings: Settings | None = None, *, reset: bool = False) -> dict[str, int]:
    config = settings or get_settings()
    payload = load_seed(config)
    if reset:
        io._run("MATCH (n) DETACH DELETE n", readonly=False)

    for label in ("Dataset", "Column", "DbtModel", "AirflowJob", "CodeAsset", "Incident", "RepairRun"):
        try:
            io._run(f"CREATE INDEX FOR (n:{label}) ON (n.id)", readonly=False)
        except Exception as exc:
            if "already" not in str(exc).casefold() and "exists" not in str(exc).casefold():
                raise

    io._run(
        """
        UNWIND $rows AS row MERGE (d:Dataset {id:row.id})
        SET d.name=row.name, d.kind=row.kind, d.platform=row.platform,
            d.description=row.description, d.schema_version=row.schema_version, d.namespace=row.namespace
        """,
        {"rows": payload["datasets"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (d:Dataset {id:row.dataset_id})
        MERGE (c:Column {id:row.id})
        SET c.dataset_id=row.dataset_id, c.name=row.name, c.native_type=row.native_type,
            c.data_type=row.data_type, c.description=row.description, c.nullable=row.nullable,
            c.ordinal=row.ordinal, c.active=row.active, c.schema_version=row.schema_version
        MERGE (d)-[:HAS_COLUMN]->(c)
        """,
        {"rows": payload["columns"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MERGE (m:DbtModel {id:row.id}) SET m.name=row.name
        WITH row,m MATCH (d:Dataset {id:row.produces}) MERGE (m)-[:PRODUCES]->(d)
        """,
        {"rows": payload["models"]},
        readonly=False,
    )
    io._run(
        "UNWIND $rows AS row MERGE (j:AirflowJob {id:row.id}) SET j.name=row.name, j.dag_id=row.dag_id",
        {"rows": payload["jobs"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MERGE (f:CodeAsset:SourceFile {id:row.id})
        SET f.path=row.path, f.kind=row.kind, f.content_sha256=row.content_sha256, f.size_bytes=row.size_bytes
        """,
        {"rows": payload["files"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (f:CodeAsset {id:row.file_id}), (a {id:row.asset_id})
        MERGE (f)-[:IMPLEMENTS]->(a)
        """,
        {"rows": payload["implements"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (a {id:row.asset_id}), (c:Column {id:row.column_id})
        MERGE (a)-[:READS]->(c)
        """,
        {"rows": payload["reads"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (a {id:row.asset_id}), (c:Column {id:row.column_id})
        MERGE (a)-[:WRITES]->(c)
        """,
        {"rows": payload["writes"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (a {id:row.asset_id}), (u {id:row.upstream_id})
        MERGE (a)-[:DEPENDS_ON]->(u)
        """,
        {"rows": payload["dependencies"]},
        readonly=False,
    )
    io._run(
        """
        UNWIND $rows AS row MATCH (s:Column {id:row.source_id}), (t:Column {id:row.target_id})
        MERGE (s)-[r:FLOWS_TO]->(t)
        SET r.operation=row.operation, r.alias_boundary=row.alias_boundary
        """,
        {"rows": payload["lineage"]},
        readonly=False,
    )
    write_snapshot(payload, config)
    return {key: len(value) for key, value in payload.items()}


def verify(io: FalkorDBIO, settings: Settings | None = None) -> dict[str, int]:
    expected = {key: len(value) for key, value in load_seed(settings).items()}
    actual = {
        "datasets": io._run("MATCH (n:Dataset) RETURN count(n)")[0][0],
        "columns": io._run("MATCH (n:Column) WHERE n.active=true RETURN count(n)")[0][0],
        "models": io._run("MATCH (n:DbtModel) RETURN count(n)")[0][0],
        "jobs": io._run("MATCH (n:AirflowJob) RETURN count(n)")[0][0],
        "files": io._run("MATCH (n:CodeAsset) RETURN count(n)")[0][0],
        "lineage": io._run("MATCH ()-[r:FLOWS_TO]->() RETURN count(r)")[0][0],
    }
    for key, value in actual.items():
        if int(value) != expected[key]:
            raise RuntimeError(f"FalkorDB seed verification failed for {key}: expected {expected[key]}, got {value}")
    return {key: int(value) for key, value in actual.items()}


def write_snapshot(payload: dict[str, list[dict[str, Any]]], settings: Settings) -> Path:
    by_dataset: dict[str, dict[str, dict[str, Any]]] = {}
    for column in payload["columns"]:
        by_dataset.setdefault(column["dataset_id"], {})[column["name"]] = {
            key: column[key] for key in ("name", "native_type", "data_type", "description", "nullable")
        }
    path = settings.warehouse_path / ".relink" / "snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(by_dataset, indent=2) + "\n", encoding="utf-8")
    return path


def _descriptions(warehouse: Path, yaml: YAML) -> dict[str, Any]:
    result: dict[str, Any] = {}
    source_doc = yaml.load(warehouse / "models" / "sources.yml")
    for source in source_doc.get("sources", []):
        database, schema = source["database"], source["schema"]
        for table in source.get("tables", []):
            name = f"{database}.{schema}.{table['name']}"
            result[name] = table.get("description", "")
            _add_column_descriptions(result, name, table.get("columns", []))
    for relative in ("models/staging/schema.yml", "models/marts/schema.yml"):
        document = yaml.load(warehouse / relative)
        for model in document.get("models", []):
            name = f"shop_prod.analytics.{model['name']}"
            result[name] = model.get("description", "")
            _add_column_descriptions(result, name, model.get("columns", []))
    return result


def _add_column_descriptions(result: dict[str, Any], dataset: str, columns: list[dict[str, Any]]) -> None:
    for column in columns:
        tests = column.get("tests", [])
        result[f"{dataset}.{column['name']}"] = {
            "description": column.get("description", ""),
            "not_null": "not_null" in tests,
        }


def _airflow_tasks(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tasks: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "task_id"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                tasks.add(keyword.value.value)
    return tasks


def _code_asset(
    repo_root: Path,
    relative_path: str,
    kind: str,
    *,
    asset_path: str | None = None,
) -> dict[str, Any]:
    path = repo_root / relative_path
    if not path.is_file():
        raise ValueError(f"code_map.yml references missing file {relative_path!r}")
    content = path.read_text(encoding="utf-8")
    if kind == "sql":
        masked, _ = mask(content)
        try:
            parse_one(masked, read="snowflake")
        except Exception as exc:
            raise ValueError(f"Unable to parse mapped dbt SQL {relative_path!r}: {exc}") from exc
    return {
        "id": file_id(asset_path or relative_path),
        "path": asset_path or relative_path,
        "kind": kind,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "size_bytes": len(content.encode()),
    }


def _unique(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    fields = keys or ("id",)
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        unique[tuple(row[field] for field in fields)] = row
    return list(unique.values())
