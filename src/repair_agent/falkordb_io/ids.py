"""Stable identifiers shared by ingestion, queries, code mapping, and reports."""

from __future__ import annotations


def dataset_id(name: str) -> str:
    return f"dataset:{name}"


def column_id(dataset_name: str, column_name: str) -> str:
    return f"column:{dataset_name}:{column_name}"


def model_id(name: str) -> str:
    return f"model:{name}"


def job_id(dag_id: str, task_id: str) -> str:
    return f"job:{dag_id}.{task_id}"


def file_id(path: str) -> str:
    return f"file:{path}"


def dataset_name(identifier: str) -> str:
    return identifier.removeprefix("dataset:")


def column_name(identifier: str) -> str:
    return identifier.rsplit(":", 1)[-1]
