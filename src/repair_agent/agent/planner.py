"""Groq planner that reasons through a real FalkorDB MCP session."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from repair_agent.agent.mcp_client import FalkorDBStdioMCPClient, MCPToolClient
from repair_agent.models import AgentActionPlan

if TYPE_CHECKING:
    from repair_agent.agent.tools import RunContext


READ_TOOL_NAMES = {
    "inspect_schema",
    "trace_column_impact",
    "explore_lineage_bfs",
    "explain_lineage_path",
    "classify_affected_asset",
    "assess_repair_candidates",
}


class GroqMCPPlanner:
    """Let Groq investigate through MCP and select a structured repair plan."""

    def __init__(self, context: RunContext, *, mcp_client: MCPToolClient | None = None) -> None:
        self.context = context
        self.client = AsyncOpenAI(
            api_key=context.settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self.mcp_client = mcp_client or FalkorDBStdioMCPClient(context.settings)

    async def investigate(self) -> AgentActionPlan:
        drift = self.context.run.drift
        impact = self.context.run.impact
        if drift is None or impact is None:
            raise RuntimeError("Groq investigation requires detected drift and FalkorDB impact evidence.")
        column = drift.old_column or drift.new_column or ""
        asset_roster = [{"asset_id": asset.urn, "name": asset.name} for asset in impact.assets]
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a schema-repair planner. Investigate only through the supplied FalkorDB MCP tools, "
                    "choose tools dynamically from prior results, and ground every asset action in returned evidence. "
                    "Use assess_repair_candidates before planning so every listed asset has a graph classification. "
                    "Use explore_lineage_bfs when breadth-first reachability adds useful algorithmic evidence. "
                    "Never request or write source code and never invent a dataset, column, path, or classification."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Investigate {drift.kind.value} drift {drift.id}: dataset_id={drift.dataset_urn}, "
                    f"column={column}, replacement={drift.new_column or 'none'}. "
                    f"Return decisions for this complete asset roster after investigation: "
                    f"{json.dumps(asset_roster, separators=(',', ':'))}"
                ),
            },
        ]
        async with self.mcp_client as mcp_client:
            advertised = await mcp_client.list_tools()
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in advertised
                if tool.name in READ_TOOL_NAMES
            ]
            available = {tool["function"]["name"] for tool in tools}
            if available != READ_TOOL_NAMES:
                missing = ", ".join(sorted(READ_TOOL_NAMES - available))
                raise RuntimeError(f"FalkorDB MCP server did not advertise required read tools: {missing}")

            exhausted = True
            for _ in range(self.context.settings.groq_max_tool_steps):
                response = await self.client.chat.completions.create(
                    model=self.context.settings.groq_model,
                    temperature=0,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,  # type: ignore[arg-type]
                    tool_choice="auto",
                )
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                if not message.tool_calls:
                    exhausted = False
                    break
                for call in message.tool_calls:
                    name = call.function.name
                    if name not in available:
                        raise ValueError(f"Groq selected unavailable MCP tool {name!r}.")
                    arguments = json.loads(call.function.arguments or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError(f"Groq returned non-object arguments for {name!r}.")
                    self.context.record_mcp_call(
                        name,
                        arguments,
                        selected_by="groq",
                        transport="mcp_stdio",
                    )
                    result = await mcp_client.call_tool(name, arguments)
                    self.context.record_mcp_result(
                        name,
                        result,
                        selected_by="groq",
                        transport="mcp_stdio",
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, separators=(",", ":"), default=str),
                        }
                    )

        if exhausted:
            self.context.emit(
                phase="impact",
                level="warning",
                title="Groq MCP tool-step limit reached",
                detail=(
                    f"Groq used {self.context.settings.groq_max_tool_steps} planning rounds; "
                    "the graph-policy verifier will reject incomplete decisions."
                ),
                data={"source": "groq_mcp_planner", "max_steps": self.context.settings.groq_max_tool_steps},
            )

        messages.append(
            {
                "role": "user",
                "content": (
                    "Submit the repair action plan now as one JSON object. Use shape "
                    '{"generated_by":"groq","decisions":[{"asset_id":"...","action":"PATCH|SKIP",'
                    '"classification":"REQUIRES_PATCH|DOWNSTREAM_UNAFFECTED|SKIPPED","reason":"...",'
                    '"evidence_tools":["tool_name"]}]}. Include exactly one decision for every roster asset. '
                    "Cite only MCP tools actually called. Do not include markdown or source code."
                ),
            }
        )
        decision_response = await self.client.chat.completions.create(
            model=self.context.settings.groq_model,
            temperature=0,
            messages=messages,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
        )
        content = decision_response.choices[0].message.content
        if not content:
            raise ValueError("Groq returned an empty repair action plan.")
        plan = AgentActionPlan.model_validate_json(content)
        self.context.run.agent_plan = plan
        self.context.emit(
            phase="impact",
            title="Groq selected graph-backed repair actions",
            detail=f"Received {len(plan.decisions)} structured action decision(s) for policy verification.",
            data={"source": "groq_action_plan", "plan": plan.model_dump(mode="json")},
        )
        return plan


async def run_groq_investigation(context: RunContext) -> AgentActionPlan | None:
    """Run optional MCP planning and degrade safely on provider or protocol errors."""

    if not context.use_llm:
        return None
    context.emit(
        phase="impact",
        title="Groq is selecting FalkorDB tools through MCP",
        detail=(
            "Tool definitions come from the MCP server; calls cross its stdio JSON-RPC transport and feed a "
            "structured action plan into deterministic graph-policy verification."
        ),
        data={"source": "groq_mcp_planner", "model": context.settings.groq_model, "transport": "mcp_stdio"},
    )
    try:
        return await GroqMCPPlanner(context).investigate()
    except Exception as exc:
        context.degrade(f"Groq MCP planning failed ({type(exc).__name__}); deterministic FalkorDB safeguards continued.")
        return None
