"""Keep demo SQL, dbt YAML, code mappings, and graph seed metadata aligned."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sqlglot
from ruamel.yaml import YAML
from sqlglot import expressions as exp

from repair_agent.config import Settings
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.falkordb_io.ingest import load_seed

REPO_ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO_ROOT / "demo-warehouse"
YAML = YAML(typ="safe")
SOURCE_PATTERN = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
REF_PATTERN = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")


def _sql_output_columns(path: Path) -> list[str]:
    sql = SOURCE_PATTERN.sub(lambda match: f"{match.group(1)}.{match.group(2)}", path.read_text(encoding="utf-8"))
    sql = REF_PATTERN.sub(lambda match: match.group(1), sql)
    parsed = sqlglot.parse_one(sql, dialect="snowflake")
    if not isinstance(parsed, exp.Select):
        raise AssertionError(f"Expected an outer SELECT in {path}, got {type(parsed).__name__}")
    return [expression.alias_or_name for expression in parsed.expressions]


def _schema_models() -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for path in (WAREHOUSE / "models/staging/schema.yml", WAREHOUSE / "models/marts/schema.yml"):
        for model in YAML.load(path)["models"]:
            assert model["name"] not in models
            models[model["name"]] = model
    return models


def test_model_columns_match_sql_yaml_and_graph_payload() -> None:
    payload = load_seed(Settings(_env_file=None))  # type: ignore[arg-type]
    schema_models = _schema_models()
    sql_paths = {path.stem: path for path in (WAREHOUSE / "models").glob("**/*.sql")}
    seeded_models = {model["name"]: model for model in payload["models"]}
    assert set(sql_paths) == set(schema_models) == set(seeded_models)
    seeded_columns: dict[str, list[str]] = {}
    for column in payload["columns"]:
        seeded_columns.setdefault(column["dataset_id"], []).append(column["name"])
    for name, path in sql_paths.items():
        assert _sql_output_columns(path) == [column["name"] for column in schema_models[name]["columns"]]
        assert _sql_output_columns(path) == seeded_columns[dataset_id(f"shop_prod.analytics.{name}")]


def test_every_model_and_column_has_descriptions_and_tests() -> None:
    for model_name, model in _schema_models().items():
        assert model.get("description"), f"{model_name} needs a model description"
        assert all(column.get("description") for column in model["columns"])
        assert any(column.get("tests") for column in model["columns"])


def test_every_lineage_endpoint_is_a_declared_column() -> None:
    payload = load_seed(Settings(_env_file=None))  # type: ignore[arg-type]
    declared = {column["id"] for column in payload["columns"]}
    assert all(edge["source_id"] in declared and edge["target_id"] in declared for edge in payload["lineage"])


def test_code_map_uses_stable_ids_and_existing_parseable_files() -> None:
    document = YAML.load(WAREHOUSE / "code_map.yml")
    assert set(document) == {"datasets", "datajobs"}
    for identifier, mapping in document["datasets"].items():
        assert identifier.startswith("dataset:")
        assert all((REPO_ROOT / mapping[key]).is_file() for key in ("sql", "schema_yml"))
    for identifier, mapping in document["datajobs"].items():
        assert identifier.startswith("job:") and (REPO_ROOT / mapping["file"]).is_file()
