from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import repair_agent.agent.runner as runner_module
import repair_agent.codegen.generator as codegen_module
from repair_agent.agent.mcp_client import MCPToolSpec
from repair_agent.agent.planner import GroqMCPPlanner
from repair_agent.agent.policy import verify_and_apply_agent_plan
from repair_agent.agent.runner import run_repair
from repair_agent.agent.tools import RunContext
from repair_agent.config import Settings
from repair_agent.drift.detect import declare_drift
from repair_agent.drift.snapshot import SchemaSnapshot
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.impact.engine import analyze
from repair_agent.models import (
    AgentActionPlan,
    AgentAssetDecision,
    ColumnSpec,
    ImpactBucket,
    PullRequestResult,
    RepairRun,
    WritebackAction,
)
from repair_agent.pipeline import run_pipeline
from repair_agent.pr.render import render_pr_body
from tests.support import OfflineFalkorIO

DRIFT_ID = "rename-orders-order_placed_at"


def _baseline(io: OfflineFalkorIO) -> SchemaSnapshot:
    root: dict[str, dict[str, ColumnSpec]] = {}
    for identifier, columns in io.schemas.items():
        restored = [
            column.model_copy(update={"name": "order_placed_at"})
            if identifier == dataset_id("shop_prod.raw.orders") and column.name == "order_created_at"
            else column
            for column in columns
        ]
        root[identifier] = {column.name: column for column in restored}
    return SchemaSnapshot(root)


def _patch_snapshot(monkeypatch, io: OfflineFalkorIO) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline(io)
    monkeypatch.setattr(SchemaSnapshot, "load", classmethod(lambda cls, path=None: baseline))


def test_deterministic_pipeline_works_without_groq_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    io = OfflineFalkorIO()
    _patch_snapshot(monkeypatch, io)
    result = run_pipeline(
        "deterministic",
        DRIFT_ID,
        graph_io=io,
        settings=Settings(_env_file=None, groq_api_key=""),  # type: ignore[arg-type]
    )
    assert result.status == "succeeded"
    assert result.patches and all(patch.valid for patch in result.patches)
    assert result.impact is not None
    report = render_pr_body(
        result.impact,
        result.patches,
        run_id=result.id,
        graph_name="test",
        timestamp=datetime.now(UTC),
    )
    assert "## Affected assets requiring patch" in report
    assert "## Proposed diffs" in report
    assert "```diff" in report and "## Correctly skipped" in report


def test_timeline_contains_falkordb_mcp_calls_and_results(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    io = OfflineFalkorIO()
    _patch_snapshot(monkeypatch, io)

    async def fake_pr(context):  # type: ignore[no-untyped-def]
        context.run.pr = PullRequestResult(mode="dry-run", url="review.md", branch="repair/test")
        return context.run.pr

    async def fake_writeback(context):  # type: ignore[no-untyped-def]
        context.run.writeback = [
            WritebackAction(
                kind="record_repair_run", target_urn=context.run.id, detail="recorded", graph_url="falkordb://test"
            )
        ]
        return context.run.writeback

    monkeypatch.setattr(runner_module, "open_pull_request_impl", fake_pr)
    monkeypatch.setattr(runner_module, "write_back_impl", fake_writeback)
    result = asyncio.run(
        run_repair(
            "timeline",
            DRIFT_ID,
            use_llm=True,
            settings=Settings(_env_file=None, groq_api_key=""),
            falkordb_io=io,  # type: ignore[arg-type]
        )
    )
    assert result.status == "succeeded"
    assert result.mode == "deterministic" and result.degraded
    calls = [event for event in result.events if event.data.get("source") == "falkordb_mcp"]
    assert any(event.title.startswith("FalkorDB MCP tool call: inspect_schema") for event in calls)
    assert any(event.title.startswith("FalkorDB MCP result: trace_column_impact") for event in calls)
    assert any(event.title.startswith("FalkorDB MCP result: explore_lineage_bfs") for event in calls)


def test_groq_cannot_author_patch_content(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    io = OfflineFalkorIO()
    _patch_snapshot(monkeypatch, io)

    async def fake_pr(context):  # type: ignore[no-untyped-def]
        context.run.pr = PullRequestResult(mode="dry-run", url="review.md", branch="repair/test")
        return context.run.pr

    async def fake_writeback(context):  # type: ignore[no-untyped-def]
        context.run.writeback = [
            WritebackAction(
                kind="record_repair_run", target_urn=context.run.id, detail="recorded", graph_url="falkordb://test"
            )
        ]
        return context.run.writeback

    monkeypatch.setattr(runner_module, "open_pull_request_impl", fake_pr)
    monkeypatch.setattr(runner_module, "write_back_impl", fake_writeback)

    async def fake_dynamic_investigation(context):  # type: ignore[no-untyped-def]
        if context.use_llm:
            context.mcp_call(
                "inspect_schema",
                {"dataset_id": dataset_id("shop_prod.raw.orders")},
                selected_by="groq",
            )

    monkeypatch.setattr(runner_module, "run_groq_investigation", fake_dynamic_investigation)
    with_groq = asyncio.run(
        run_repair(
            "groq",
            DRIFT_ID,
            use_llm=True,
            settings=Settings(_env_file=None, groq_api_key="not-used-in-codegen"),
            falkordb_io=io,  # type: ignore[arg-type]
        )
    )
    without_groq = asyncio.run(
        run_repair(
            "no-groq",
            DRIFT_ID,
            use_llm=False,
            settings=Settings(_env_file=None, groq_api_key=""),
            falkordb_io=io,  # type: ignore[arg-type]
        )
    )
    assert [(patch.file_path, patch.after) for patch in with_groq.patches] == [
        (patch.file_path, patch.after) for patch in without_groq.patches
    ]
    source = inspect.getsource(codegen_module)
    assert "Groq" not in source and "openai" not in source and "agent.prose" not in source
    assert any(event.data.get("selected_by") == "groq" for event in with_groq.events)


def test_groq_planner_dynamically_executes_selected_mcp_tool() -> None:
    io = OfflineFalkorIO()
    run = RepairRun(
        id="dynamic-tools",
        drift=declare_drift(
            kind="RENAME",
            dataset_urn=dataset_id("shop_prod.raw.orders"),
            old_column="order_placed_at",
            new_column="order_created_at",
        ),
    )
    run.impact = analyze(run.drift, io)  # type: ignore[arg-type]
    settings = Settings(_env_file=None, groq_api_key="test", groq_max_tool_steps=3)  # type: ignore[arg-type]
    context = RunContext(
        run=run,
        drift_id=DRIFT_ID,
        falkordb_io=io,  # type: ignore[arg-type]
        settings=settings,
        on_event=None,
        pr_mode="dry-run",
        use_llm=True,
        model_name=settings.groq_model,
    )

    class FakeMessage:
        def __init__(self, tool_calls, content=None):  # type: ignore[no-untyped-def]
            self.tool_calls = tool_calls
            self.content = content

        def model_dump(self, **_kwargs):  # type: ignore[no-untyped-def]
            return {"role": "assistant", "tool_calls": self.tool_calls} if self.tool_calls else {"role": "assistant"}

    selected = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="explore_lineage_bfs",
            arguments=json.dumps(
                {"dataset_id": dataset_id("shop_prod.raw.orders"), "column": "order_placed_at", "max_depth": 8}
            ),
        ),
    )

    decisions = [
        {
            "asset_id": asset.urn,
            "action": "PATCH" if asset.bucket is ImpactBucket.REQUIRES_PATCH else "SKIP",
            "classification": asset.bucket.value,
            "reason": asset.reason,
            "evidence_tools": ["explore_lineage_bfs"],
        }
        for asset in run.impact.assets
    ]

    class FakeCompletions:
        def __init__(self) -> None:
            self.responses = [
                FakeMessage([selected]),
                FakeMessage([]),
                FakeMessage([], json.dumps({"generated_by": "groq", "decisions": decisions})),
            ]
            self.requests: list[dict[str, object]] = []

        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            self.requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=self.responses.pop(0))])

    class FakeMCPClient:
        def __init__(self) -> None:
            self.called: list[tuple[str, dict[str, object]]] = []

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        async def list_tools(self):  # type: ignore[no-untyped-def]
            properties = {"dataset_id": {"type": "string"}, "column": {"type": "string"}}
            return [
                MCPToolSpec(name=name, description=name, input_schema={"type": "object", "properties": properties})
                for name in (
                    "inspect_schema",
                    "trace_column_impact",
                    "explore_lineage_bfs",
                    "explain_lineage_path",
                    "classify_affected_asset",
                    "assess_repair_candidates",
                )
            ]

        async def call_tool(self, name, arguments):  # type: ignore[no-untyped-def]
            self.called.append((name, arguments))
            return io.explore_lineage_bfs(arguments["dataset_id"], arguments["column"], max_depth=arguments["max_depth"])

    completions = FakeCompletions()
    mcp_client = FakeMCPClient()
    planner = GroqMCPPlanner(context, mcp_client=mcp_client)  # type: ignore[arg-type]
    planner.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    asyncio.run(planner.investigate())

    selected_events = [event for event in run.events if event.data.get("selected_by") == "groq"]
    assert any(event.data.get("tool") == "explore_lineage_bfs" for event in selected_events)
    assert all(event.data.get("transport") == "mcp_stdio" for event in selected_events)
    assert mcp_client.called[0][0] == "explore_lineage_bfs"
    assert run.agent_plan is not None and len(run.agent_plan.decisions) == len(run.impact.assets)
    assert len(completions.requests) == 3
    assert completions.requests[0]["tool_choice"] == "auto"


def test_only_graph_verified_agent_actions_reach_codegen() -> None:
    io = OfflineFalkorIO()
    drift = declare_drift(
        kind="RENAME",
        dataset_urn=dataset_id("shop_prod.raw.orders"),
        old_column="order_placed_at",
        new_column="order_created_at",
    )
    run = RepairRun(id="policy", drift=drift, impact=analyze(drift, io))  # type: ignore[arg-type]
    run.events.extend([])
    context = RunContext(
        run=run,
        drift_id=DRIFT_ID,
        falkordb_io=io,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        on_event=None,
        pr_mode="dry-run",
        use_llm=True,
        model_name="test",
    )
    context.record_mcp_result(
        "assess_repair_candidates",
        {"decisions": []},
        selected_by="groq",
        transport="mcp_stdio",
    )
    run.agent_plan = AgentActionPlan(
        decisions=[
            AgentAssetDecision(
                asset_id=asset.urn,
                action="PATCH" if asset.bucket is ImpactBucket.REQUIRES_PATCH else "SKIP",
                classification=asset.bucket,
                reason=asset.reason,
                evidence_tools=["assess_repair_candidates"],
            )
            for asset in run.impact.assets
        ]
    )
    assert verify_and_apply_agent_plan(context) is True
    assert run.agent_plan.verified is True
    assert any(event.title == "Graph-evidenced agent action plan approved" for event in run.events)

    run.agent_plan.decisions[0].action = "SKIP" if run.agent_plan.decisions[0].action == "PATCH" else "PATCH"
    assert verify_and_apply_agent_plan(context) is False
    assert run.agent_plan.verified is False
