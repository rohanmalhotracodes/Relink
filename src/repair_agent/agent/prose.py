"""Groq-owned prose with deterministic Jinja fallbacks; code is never sent to the LLM."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TypeVar

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, Field

from repair_agent.config import Settings
from repair_agent.models import DriftEvent, ImpactReport, RepairRun

LOGGER = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class PRNarrative(BaseModel):
    title: str
    summary: str
    risk_note: str
    reviewer_checklist: list[str] = Field(default_factory=list)


class MigrationDoc(BaseModel):
    title: str
    markdown: str


class ColumnDoc(BaseModel):
    text: str


class GroqProseProvider:
    """Small OpenAI-compatible Groq adapter restricted to structured prose."""

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        self.model = settings.groq_model
        self.client = AsyncOpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1")

    async def generate(self, output_type: type[T], instructions: str, facts: str) -> T:
        schema = output_type.model_json_schema()
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{instructions}\nReturn JSON matching this schema: {json.dumps(schema)}. "
                        "You receive no source code and cannot create or alter patches."
                    ),
                },
                {"role": "user", "content": facts},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return output_type.model_validate_json(content)


PR_FALLBACK = "Repair {{ dataset }}: {{ old or '∅' }} → {{ new or '∅' }}"
SUMMARY_FALLBACK = (
    "The upstream {{ kind|lower }} was traced through FalkorDB column lineage. The deterministic engine generated "
    "{{ patch_count }} surgical patch(es), while {{ unaffected }} downstream model(s) were proven insulated and "
    "{{ skipped }} asset(s) were skipped with explicit graph evidence."
)
RISK_FALLBACK = (
    "Risk is bounded by the validator hard gate: {{ resolved }}/{{ total }} generated references resolved and no "
    "unresolved column is allowed into the PR. Review the lineage-derived skip reasons and deploy dbt models before "
    "dependent Airflow jobs."
)
MIGRATION_FALLBACK = """# {{ title }}

## Change

{{ rationale }}

The repair changes `{{ old or '∅' }}` to `{{ new or '∅' }}` for `{{ dataset }}`.
Code was produced only by deterministic sqlglot/dbt/Airflow transforms; language-model
output is prose-only.

## Blast radius

- **{{ requires_patch }}** code-bearing asset(s) require a patch.
- **{{ unaffected }}** downstream asset(s) are insulated by existing aliases and need review only.
- **{{ skipped }}** asset(s) were skipped because FalkorDB contains no changed-column path.

## Validation and rollout

All {{ total }} generated SQL reference(s) were checked before review; {{ resolved }} resolved.
Apply the dbt changes before dependent Airflow code, run the declared tests, and monitor
the graph incident until consumers are healthy.

## Rollback

Revert the PR commit, restore the upstream field contract, and re-run the repair so the
FalkorDB schema and recorded run state match the restored source.
"""
COLUMN_FALLBACK = (
    "{{ change }} by Relink in run {{ run_id }} — see {{ pr_url }}. "
    "Validated against FalkorDB's active schema before review."
)


async def generate_pr_narrative(
    run: RepairRun,
    settings: Settings,
    *,
    model: str,
    use_llm: bool,
    on_degradation: Callable[[str], None] | None = None,
) -> PRNarrative:
    del model
    fallback = fallback_pr_narrative(run)
    if not use_llm or not settings.groq_api_key.strip():
        return fallback
    return await _generate(
        PRNarrative,
        settings,
        "Write concise reviewer-facing schema-repair prose using only supplied facts.",
        _prose_context(run),
        fallback,
        on_degradation,
    )


async def generate_migration_doc(
    run: RepairRun,
    settings: Settings,
    *,
    model: str,
    use_llm: bool,
    on_degradation: Callable[[str], None] | None = None,
) -> MigrationDoc:
    del model
    fallback = fallback_migration_doc(run)
    if not use_llm or not settings.groq_api_key.strip():
        return fallback
    return await _generate(
        MigrationDoc,
        settings,
        "Write an operational migration note with rollout, validation, monitoring, and rollback.",
        _prose_context(run),
        fallback,
        on_degradation,
    )


async def generate_column_doc(
    run: RepairRun,
    settings: Settings,
    *,
    model: str,
    use_llm: bool,
    pr_url: str,
    on_degradation: Callable[[str], None] | None = None,
) -> ColumnDoc:
    del model
    fallback = fallback_column_doc(run, pr_url)
    if not use_llm or not settings.groq_api_key.strip():
        return fallback
    return await _generate(
        ColumnDoc,
        settings,
        "Write one concise graph catalog description. Do not invent semantics or owners.",
        f"{_prose_context(run)}\nPR URL: {pr_url}",
        fallback,
        on_degradation,
    )


async def _generate(
    output_type: type[T],
    settings: Settings,
    instructions: str,
    facts: str,
    fallback: T,
    on_degradation: Callable[[str], None] | None,
) -> T:
    try:
        return await GroqProseProvider(settings).generate(output_type, instructions, facts)
    except Exception as exc:
        reason = f"Groq prose generation failed ({type(exc).__name__}); deterministic prose was used."
        LOGGER.warning(reason)
        if on_degradation:
            on_degradation(reason)
        return fallback


def fallback_pr_narrative(run: RepairRun) -> PRNarrative:
    drift, impact = _required(run)
    references = [reference for patch in run.patches for reference in patch.references]
    values = {
        "dataset": drift.dataset_name,
        "old": drift.old_column,
        "new": drift.new_column,
        "kind": drift.kind.value,
        "patch_count": len(run.patches),
        "unaffected": impact.stats.get("downstream_unaffected", 0),
        "skipped": impact.stats.get("skipped", 0),
        "resolved": sum(reference.status == "OK" for reference in references),
        "total": len(references),
    }
    return PRNarrative(
        title=_render(PR_FALLBACK, values),
        summary=_render(SUMMARY_FALLBACK, values),
        risk_note=_render(RISK_FALLBACK, values),
        reviewer_checklist=[
            "Confirm the upstream schema change and rollout order with the source owner.",
            "Review every affected, alias-insulated, and skipped reason against FalkorDB evidence.",
            "Run dbt validation and the dependent Airflow task in staging before production deployment.",
            "Verify the FalkorDB incident and repair-run record after merge.",
        ],
    )


def fallback_migration_doc(run: RepairRun) -> MigrationDoc:
    drift, impact = _required(run)
    references = [reference for patch in run.patches for reference in patch.references]
    title = f"Migration: {drift.dataset_name}.{drift.old_column or drift.new_column}"
    markdown = _render(
        MIGRATION_FALLBACK,
        {
            "title": title,
            "rationale": drift.rationale,
            "dataset": drift.dataset_name,
            "old": drift.old_column,
            "new": drift.new_column,
            "requires_patch": impact.stats.get("requires_patch", 0),
            "unaffected": impact.stats.get("downstream_unaffected", 0),
            "skipped": impact.stats.get("skipped", 0),
            "resolved": sum(reference.status == "OK" for reference in references),
            "total": len(references),
        },
    )
    return MigrationDoc(title=title, markdown=markdown.rstrip() + "\n")


def fallback_column_doc(run: RepairRun, pr_url: str) -> ColumnDoc:
    drift, _ = _required(run)
    if drift.kind.value == "RENAME":
        change = f"Renamed from `{drift.old_column}` to `{drift.new_column}`"
    elif drift.kind.value == "RETYPE":
        change = f"Retyped from `{drift.old_type}` to `{drift.new_type}`"
    else:
        change = f"Deprecated after upstream `{drift.old_column}` was dropped"
    return ColumnDoc(text=_render(COLUMN_FALLBACK, {"change": change, "run_id": run.id, "pr_url": pr_url}))


def _required(run: RepairRun) -> tuple[DriftEvent, ImpactReport]:
    if run.drift is None or run.impact is None:
        raise RuntimeError("Prose generation requires detected drift and impact results.")
    return run.drift, run.impact


def _prose_context(run: RepairRun) -> str:
    drift, impact = _required(run)
    assets = "\n".join(f"- {asset.bucket.value}: {asset.name} — {asset.reason}" for asset in impact.assets)
    strategies = "\n".join(f"- {patch.file_path}: {patch.strategy}" for patch in run.patches)
    references = [reference for patch in run.patches for reference in patch.references]
    return (
        f"Run: {run.id}\nDrift: {drift.kind.value} {drift.dataset_name}.{drift.old_column} -> {drift.new_column}\n"
        f"Rationale: {drift.rationale}\nImpact:\n{assets}\nPatch strategies (no code):\n{strategies}\n"
        f"Validation: {sum(reference.status == 'OK' for reference in references)}/{len(references)} resolved."
    )


def _render(template: str, values: dict[str, object]) -> str:
    return Environment(undefined=StrictUndefined, autoescape=False).from_string(template).render(**values).strip()
