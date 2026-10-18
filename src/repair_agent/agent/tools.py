"""Repair orchestration helpers and FalkorDB MCP timeline instrumentation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from repair_agent.agent.prose import generate_migration_doc, generate_pr_narrative
from repair_agent.config import Settings
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.tools import FalkorDBTools
from repair_agent.github_app import GitHubAppClient
from repair_agent.models import FileChange, PRRequest, PullRequestResult, RepairRun, RunEvent, WritebackAction
from repair_agent.pr.dry_run import DryRunPRProvider
from repair_agent.pr.gh_cli import GhCliPRProvider
from repair_agent.pr.github_comment import GitHubCommentProvider
from repair_agent.pr.render import render_pr_body


@dataclass
class RunContext:
    run: RepairRun
    drift_id: str
    falkordb_io: FalkorDBIO
    settings: Settings
    on_event: Callable[[RunEvent], None] | None
    pr_mode: str
    use_llm: bool
    model_name: str
    github_repository: str = ""
    github_issue_number: int | None = None
    github_app_client: GitHubAppClient | None = None
    github_pull_request_number: int | None = None
    github_head_repository: str = ""
    github_head_ref: str = ""

    def emit(
        self,
        *,
        phase: str,
        title: str,
        detail: str = "",
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        event = RunEvent(
            seq=len(self.run.events) + 1,
            phase=phase,
            title=title,
            detail=detail,
            level=level,
            data=data or {},  # type: ignore[arg-type]
        )
        self.run.events.append(event)
        if self.on_event:
            self.on_event(event)
        return event

    def degrade(self, reason: str) -> None:
        self.run.degraded = True
        if reason not in self.run.degradations:
            self.run.degradations.append(reason)
        self.emit(phase="detect", level="warning", title="Graceful degradation", detail=reason)

    def mcp_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        selected_by: str = "deterministic_safeguard",
    ) -> dict[str, Any]:
        self.record_mcp_call(name, arguments, selected_by=selected_by, transport="in_process_safeguard")
        method = getattr(FalkorDBTools(self.falkordb_io), name)
        result = method(**arguments)
        self.record_mcp_result(name, result, selected_by=selected_by, transport="in_process_safeguard")
        return result

    def record_mcp_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        selected_by: str,
        transport: str,
    ) -> None:
        self.emit(
            phase="impact" if name != "record_repair_result" else "writeback",
            title=f"FalkorDB MCP tool call: {name}",
            detail=f"Calling `{name}` with graph-scoped structured arguments.",
            data={
                "tool": name,
                "source": "falkordb_mcp",
                "selected_by": selected_by,
                "transport": transport,
                "arguments": arguments,
            },
        )

    def record_mcp_result(
        self,
        name: str,
        result: dict[str, Any],
        *,
        selected_by: str,
        transport: str,
    ) -> None:
        summary = _result_summary(result)
        self.emit(
            phase="impact" if name != "record_repair_result" else "writeback",
            level="debug",
            title=f"FalkorDB MCP result: {name}",
            detail=summary,
            data={
                "tool": name,
                "source": "falkordb_mcp",
                "selected_by": selected_by,
                "transport": transport,
                "result": result,
            },
        )


async def open_pull_request_impl(context: RunContext) -> PullRequestResult:
    if context.run.pr is not None:
        return context.run.pr
    drift, impact = context.run.drift, context.run.impact
    if drift is None or impact is None:
        raise RuntimeError("PR creation requires drift and impact state.")
    if context.run.patches and not all(patch.valid for patch in context.run.patches):
        context.run.pr = PullRequestResult(
            mode="dry-run" if context.pr_mode != "live" else "live",
            url="",
            branch=f"repair/{drift.id}",
            title="Validation blocked schema-drift repair",
            files=[patch.file_path for patch in context.run.patches],
            ok=False,
            error="Validation did not pass; no PR was opened.",
            state="blocked",
        )
        await _deliver_github_comment(context)
        return context.run.pr
    if not context.run.patches and impact.stats.get("requires_patch", 0) == 0:
        context.run.pr = PullRequestResult(
            mode="dry-run" if context.pr_mode != "live" else "live",
            url="",
            branch=f"repair/{drift.id}",
            title="No changes required — no PR opened",
            ok=True,
            state="no_changes_required",
        )
        await _deliver_github_comment(context)
        return context.run.pr

    context.emit(
        phase="pr",
        title="Preparing the review package",
        detail="Rendering graph paths, skip reasons, deterministic diffs, and validation evidence.",
    )
    narrative = await generate_pr_narrative(
        context.run,
        context.settings,
        model=context.model_name,
        use_llm=context.use_llm,
        on_degradation=context.degrade,
    )
    body = render_pr_body(
        impact,
        context.run.patches,
        run_id=context.run.id,
        graph_name=context.settings.falkordb_graph,
        timestamp=datetime.now(UTC),
        narrative_summary=narrative.summary,
        risk_note=narrative.risk_note,
        reviewer_checklist=narrative.reviewer_checklist,
    )
    request = PRRequest(
        branch=f"repair/{drift.id}",
        base="main",
        title=narrative.title,
        body_markdown=body,
        files=[
            FileChange(path=patch.file_path, content=patch.after, previous_content=patch.before)
            for patch in context.run.patches
        ],
        commit_message=f"Repair schema drift {drift.id}",
    )
    if (
        context.github_app_client is not None
        and context.github_pull_request_number is not None
        and context.github_head_repository
        and context.github_head_ref
    ):
        commit_url = await context.github_app_client.commit_files(
            context.github_head_repository,
            context.github_head_ref,
            request.files,
            request.commit_message,
        )
        context.run.pr = PullRequestResult(
            mode="live",
            url=f"https://github.com/{context.github_repository}/pull/{context.github_pull_request_number}",
            branch=context.github_head_ref,
            title=request.title,
            number=context.github_pull_request_number,
            files=[change.path for change in request.files],
            ok=True,
            state="opened",
        )
        context.emit(
            phase="pr",
            title="Repair committed to the source pull request",
            detail=commit_url,
            data={"mode": "live", "url": context.run.pr.url, "commit_url": commit_url},
        )
        await _deliver_github_comment(context)
        return context.run.pr
    dry_run = DryRunPRProvider(
        context.settings.repo_root / ".relink" / "pr_bodies", repo_root=context.settings.repo_root
    )
    provider = (
        GhCliPRProvider(context.settings.repo_root, fallback=dry_run, on_degradation=context.degrade)
        if context.pr_mode == "live"
        else dry_run
    )
    context.run.pr = await asyncio.to_thread(provider.open_pr, request)
    context.emit(
        phase="pr",
        level="info" if context.run.pr.ok else "error",
        title="Review package ready" if context.run.pr.ok else "PR provider failed",
        detail=context.run.pr.url if context.run.pr.ok else context.run.pr.error or "Unknown PR error.",
        data={"mode": context.run.pr.mode, "url": context.run.pr.url},
    )
    await _deliver_github_comment(context)
    return context.run.pr


async def _deliver_github_comment(context: RunContext) -> None:
    """Create local issue and PR conversations after review state is known."""

    live = context.pr_mode == "live" and context.settings.github_comment_mode == "live"
    repository = context.github_repository or context.settings.github_repository
    issue_text = context.settings.github_issue_number.strip()
    target_number = context.github_issue_number
    if target_number is None and issue_text.isdigit():
        target_number = int(issue_text)
    # Local demo mode deliberately needs no GitHub account. These identifiers are only
    # labels for the simulated conversation and never trigger a network request.
    if not live:
        repository = repository or "acme/warehouse"
        target_number = target_number or 183
    pr_target_number = context.run.pr.number if context.run.pr and context.run.pr.number else target_number
    if not live and pr_target_number == target_number:
        pr_target_number = (target_number or 183) + 1
    provider = GitHubCommentProvider(
        context.settings.repo_root / ".relink" / "github_comments",
        repo_root=context.settings.repo_root,
    )
    context.run.github_issue_delivery = await asyncio.to_thread(
        provider.deliver,
        context.run,
        repository=repository,
        target_number=target_number,
        public_url=context.settings.repair_agent_public_url,
        live=live,
        kind="issue",
    )
    context.run.github_pr_delivery = await asyncio.to_thread(
        provider.deliver,
        context.run,
        repository=repository,
        target_number=pr_target_number,
        public_url=context.settings.repair_agent_public_url,
        live=live,
        kind="pull_request",
    )
    context.run.github_delivery = context.run.github_pr_delivery
    for delivery, label in (
        (context.run.github_issue_delivery, "issue impact comment"),
        (context.run.github_pr_delivery, "pull-request repair comment"),
    ):
        context.emit(
            phase="pr",
            level="info" if delivery.ok else "warning",
            title=(f"GitHub {label} posted" if delivery.mode == "live" else f"Local {label} prepared"),
            detail=delivery.target_url or delivery.artifact_path or delivery.error or "No delivery target configured.",
            data={
                "source": "github",
                "kind": delivery.kind,
                "mode": delivery.mode,
                "repository": delivery.repository,
                "target_number": delivery.target_number,
                "url": delivery.target_url,
            },
        )


async def write_back_impl(context: RunContext) -> list[WritebackAction]:
    if context.run.writeback:
        return context.run.writeback
    if context.run.pr is None:
        await open_pull_request_impl(context)
    drift, impact, pr = context.run.drift, context.run.impact, context.run.pr
    if drift is None or impact is None or pr is None:
        raise RuntimeError("Graph recording requires drift, impact, and PR state.")

    migration = await generate_migration_doc(
        context.run,
        context.settings,
        model=context.model_name,
        use_llm=context.use_llm,
        on_degradation=context.degrade,
    )
    migration_path = context.settings.repo_root / ".relink" / "migration_docs" / f"{drift.id}.md"
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    migration_path.write_text(migration.markdown, encoding="utf-8")

    context.falkordb_io.record_incident(
        incident_id=drift.id,
        dataset_id=drift.dataset_urn,
        column=drift.old_column or drift.new_column or "schema",
        kind=drift.kind.value,
    )
    patched_ids = sorted({patch.asset_urn for patch in context.run.patches})
    context.mcp_call(
        "record_repair_result",
        {
            "run_id": context.run.id,
            "incident_id": drift.id,
            "status": "succeeded" if pr.ok else "failed",
            "pr_url": pr.url,
            "patched_asset_ids": patched_ids,
        },
    )
    graph_url = f"falkordb://{context.settings.falkordb_graph}"
    context.run.writeback = [
        WritebackAction(
            kind="record_incident",
            target_urn=drift.dataset_urn,
            detail=f"Recorded `{drift.id}` and its affected dataset in FalkorDB.",
            graph_url=graph_url,
        ),
        WritebackAction(
            kind="record_repair_run",
            target_urn=context.run.id,
            detail=f"Recorded status, review URL, and {len(patched_ids)} PATCHED relationship(s).",
            graph_url=graph_url,
        ),
        WritebackAction(
            kind="attach_migration_doc",
            target_urn=drift.dataset_urn,
            detail=f"Wrote reviewable migration guidance to {migration_path.relative_to(context.settings.repo_root)}.",
            graph_url=graph_url,
        ),
    ]
    for action in context.run.writeback:
        context.emit(
            phase="writeback",
            title=f"FalkorDB state: {action.kind}",
            detail=action.detail,
            data={"ok": action.ok, "graph_url": action.graph_url},
        )
    return context.run.writeback


def _result_summary(result: dict[str, Any]) -> str:
    if "paths" in result:
        return f"Returned {len(result['paths'])} graph path(s)."
    if "columns" in result:
        return f"Returned {len(result['columns'])} active column(s)."
    if "classification" in result:
        return f"Classification: {result['classification']}."
    if result.get("algorithm"):
        return f"{result['algorithm']} reached {len(result.get('column_ids', []))} column node(s)."
    if "decisions" in result:
        return f"Returned {len(result['decisions'])} graph-backed asset decision(s)."
    if result.get("recorded"):
        return f"Recorded repair run {result.get('run_id')}."
    return "Structured graph evidence returned."
