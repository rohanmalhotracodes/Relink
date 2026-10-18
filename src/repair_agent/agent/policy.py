"""Deterministic policy checks for Groq's structured repair decisions."""

from __future__ import annotations

from repair_agent.agent.tools import RunContext
from repair_agent.models import ImpactBucket


def verify_and_apply_agent_plan(context: RunContext) -> bool:
    """Approve a Groq plan only when every action is supported by graph evidence.

    A verified plan becomes the action input consumed by deterministic code generation.
    Invalid or incomplete plans are retained for review but cannot influence a patch.
    """

    plan = context.run.agent_plan
    impact = context.run.impact
    if plan is None or impact is None:
        return False

    errors: list[str] = []
    expected = {asset.urn: asset for asset in impact.assets}
    decisions = {decision.asset_id: decision for decision in plan.decisions}
    if len(decisions) != len(plan.decisions):
        errors.append("The plan contains duplicate asset decisions.")

    selected_tools = {
        str(event.data["tool"])
        for event in context.run.events
        if event.data.get("selected_by") == "groq" and event.data.get("transport") == "mcp_stdio" and "result" in event.data
    }
    for asset_id, asset in expected.items():
        decision = decisions.get(asset_id)
        if decision is None:
            errors.append(f"Missing decision for {asset_id}.")
            continue
        expected_action = "PATCH" if asset.bucket is ImpactBucket.REQUIRES_PATCH else "SKIP"
        if decision.action != expected_action:
            errors.append(
                f"{asset_id}: action {decision.action} conflicts with FalkorDB classification {asset.bucket.value}."
            )
        if decision.classification is not asset.bucket:
            errors.append(
                f"{asset_id}: claimed {decision.classification.value}, FalkorDB established {asset.bucket.value}."
            )
        if not decision.evidence_tools:
            errors.append(f"{asset_id}: no MCP evidence tool was cited.")
        elif unknown := set(decision.evidence_tools) - selected_tools:
            errors.append(f"{asset_id}: cited MCP tools were not called: {', '.join(sorted(unknown))}.")
    for asset_id in decisions.keys() - expected.keys():
        errors.append(f"Plan references unknown asset {asset_id}.")

    plan.verification_errors = errors
    plan.verified = not errors
    if errors:
        context.degrade(
            "Groq's structured action plan failed deterministic graph-policy verification; "
            "the authoritative FalkorDB classifications remained in control."
        )
        context.emit(
            phase="impact",
            level="warning",
            title="Agent action plan rejected",
            detail=f"{len(errors)} policy conflict(s) prevented model-selected actions from reaching code generation.",
            data={"source": "agent_policy", "verified": False, "errors": errors},
        )
        return False

    # Make the verified plan the explicit input to code generation. The assignment
    # looks redundant because policy requires agreement, but it establishes the
    # security boundary: model-selected actions are used only after graph validation.
    for asset in impact.assets:
        asset.bucket = decisions[asset.urn].classification
    context.emit(
        phase="impact",
        title="Graph-evidenced agent action plan approved",
        detail=(
            f"Groq selected {sum(decision.action == 'PATCH' for decision in plan.decisions)} patch action(s); "
            "FalkorDB policy verified every action and passed the plan to the AST executor."
        ),
        data={
            "source": "agent_policy",
            "verified": True,
            "decisions": [decision.model_dump(mode="json") for decision in plan.decisions],
        },
    )
    return True
