"""End-to-end runner with deterministic graph safeguards and optional Groq prose."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from repair_agent.agent.planner import run_groq_investigation
from repair_agent.agent.policy import verify_and_apply_agent_plan
from repair_agent.agent.tools import RunContext, open_pull_request_impl, write_back_impl
from repair_agent.config import Settings, get_settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.github_app import GitHubAppClient
from repair_agent.models import RepairRun, RunEvent
from repair_agent.pipeline import codegen_stage, detect_stage, impact_stage, validate_stage

LOGGER = logging.getLogger(__name__)


async def run_repair(
    run_id: str,
    drift_id: str,
    *,
    use_llm: bool,
    pr_mode: str | None = None,
    settings: Settings | None = None,
    falkordb_io: FalkorDBIO | None = None,
    github_repository: str = "",
    github_issue_number: int | None = None,
    github_app_client: GitHubAppClient | None = None,
    github_pull_request_number: int | None = None,
    github_head_repository: str = "",
    github_head_ref: str = "",
    on_event: Callable[[RunEvent], None] | None = None,
    run: RepairRun | None = None,
) -> RepairRun:
    """Run graph inspection → deterministic repair → review → graph recording."""

    active_settings = settings or get_settings()
    io = falkordb_io or FalkorDBIO(active_settings)
    active_run = run or RepairRun(id=run_id)
    active_run.mode = "agent" if use_llm and active_settings.groq_api_key.strip() else "deterministic"
    context = RunContext(
        run=active_run,
        drift_id=drift_id,
        falkordb_io=io,
        settings=active_settings,
        on_event=on_event,
        pr_mode=pr_mode or active_settings.pr_mode,
        use_llm=use_llm and bool(active_settings.groq_api_key.strip()),
        model_name=active_settings.groq_model,
        github_repository=github_repository,
        github_issue_number=github_issue_number,
        github_app_client=github_app_client,
        github_pull_request_number=github_pull_request_number,
        github_head_repository=github_head_repository,
        github_head_ref=github_head_ref,
    )
    context.emit(
        phase="detect",
        title="Schema-drift repair started",
        detail=f"Run {run_id} is scoped to `{drift_id}` in {context.pr_mode} PR mode.",
    )
    if use_llm and not active_settings.groq_api_key.strip():
        context.degrade(
            "GROQ_API_KEY is absent; graph reasoning, deterministic patches, validation, dry-run review, "
            "and FalkorDB recording continue without the prose model."
        )
    elif context.use_llm:
        context.emit(
            phase="detect",
            title=f"Groq prose enabled: {active_settings.groq_model}",
            detail="Groq receives facts and patch strategies only; Patch.after code is never sent or model-authored.",
        )
    try:
        await asyncio.to_thread(detect_stage, active_run, drift_id, io, active_settings, on_event)
        drift = active_run.drift
        if drift is None:
            raise RuntimeError("Detection completed without a drift event.")
        # Mandatory graph reads establish the minimum evidence needed even when
        # Groq is unavailable, skips a tool, or exhausts its planning budget.
        await asyncio.to_thread(context.mcp_call, "inspect_schema", {"dataset_id": drift.dataset_urn})
        await asyncio.to_thread(
            context.mcp_call,
            "trace_column_impact",
            {"dataset_id": drift.dataset_urn, "column": drift.old_column or drift.new_column or "", "max_hops": 8},
        )
        await asyncio.to_thread(
            context.mcp_call,
            "explore_lineage_bfs",
            {"dataset_id": drift.dataset_urn, "column": drift.old_column or drift.new_column or "", "max_depth": 8},
        )
        await asyncio.to_thread(impact_stage, active_run, io, active_settings, on_event)
        if active_run.impact is None:
            raise RuntimeError("Impact analysis completed without a report.")
        await run_groq_investigation(context)
        if active_run.agent_plan is not None:
            verify_and_apply_agent_plan(context)
        for asset in active_run.impact.assets:
            await asyncio.to_thread(
                context.mcp_call,
                "classify_affected_asset",
                {
                    "dataset_id": drift.dataset_urn,
                    "column": drift.old_column or drift.new_column or "",
                    "asset_id": asset.urn,
                },
            )
        await asyncio.to_thread(codegen_stage, active_run, active_settings, on_event)
        await asyncio.to_thread(validate_stage, active_run, io, active_settings, on_event)
        await open_pull_request_impl(context)
        await write_back_impl(context)
        active_run.status = "succeeded"
    except Exception as exc:
        active_run.status = "failed"
        active_run.error = str(exc)
        active_run.failed_stage = active_run.failed_stage or _current_stage(active_run)
        context.emit(
            phase="done",
            level="error",
            title="Repair run failed",
            detail=f"{exc}. No live PR was opened unless the PR stage had already completed.",
        )
        LOGGER.exception("Repair run %s failed", run_id)
    active_run.finished_at = datetime.now(UTC)
    active_run.completed_stages = _completed_stages(active_run)
    context.emit(
        phase="done",
        level="info" if active_run.status == "succeeded" else "error",
        title="Repair run complete" if active_run.status == "succeeded" else "Repair run FAILED",
        detail=(
            f"Status {active_run.status}; {len(active_run.patches)} patches; "
            f"{len(active_run.writeback)} graph-state actions; mode={active_run.mode}."
        ),
        data={
            "status": active_run.status,
            "mode": active_run.mode,
            "degraded": active_run.degraded,
            "error": active_run.error,
            "failed_stage": active_run.failed_stage,
        },
    )
    return active_run


def _completed_stages(run: RepairRun) -> list[str]:
    done: list[str] = []
    if run.drift:
        done.append("detect")
    if run.impact:
        done.append("impact")
    nothing = bool(run.impact and run.impact.stats.get("requires_patch", 0) == 0 and not run.patches)
    if run.patches or nothing:
        done.append("codegen")
    if nothing or (run.patches and all(patch.valid for patch in run.patches)):
        done.append("validate")
    if run.pr and run.pr.ok:
        done.append("pr")
    if run.writeback and all(action.ok for action in run.writeback):
        done.append("writeback")
    return done


def _current_stage(run: RepairRun) -> str:
    completed = set(_completed_stages(run))
    for name in ("detect", "impact", "codegen", "validate", "pr", "writeback"):
        if name not in completed:
            return name
    return "done"
