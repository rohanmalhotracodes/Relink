"""Failure surfacing, review packaging, and API transport regressions."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from repair_agent.agent.runner import _completed_stages
from repair_agent.api.app import create_app
from repair_agent.config import Settings
from repair_agent.drift.detect import declare_drift
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.models import (
    DriftKind,
    FileChange,
    ImpactReport,
    LineageGraph,
    Patch,
    PRRequest,
    PullRequestResult,
    ReferenceCheck,
    RepairRun,
    RunEvent,
)
from repair_agent.pr.dry_run import DryRunPRProvider

DRIFT_ID = "rename-orders-order_placed_at"
api_module = importlib.import_module("repair_agent.api.app")


def _drift():
    return declare_drift(
        kind=DriftKind.RENAME,
        dataset_urn=dataset_id("shop_prod.raw.orders"),
        old_column="order_placed_at",
        new_column="order_created_at",
        old_type="TIMESTAMP_NTZ",
        new_type="TIMESTAMP_NTZ",
    )


def _impact(requires_patch: int = 0) -> ImpactReport:
    return ImpactReport(
        drift=_drift(),
        assets=[],
        graph=LineageGraph(),
        stats={"requires_patch": requires_patch, "downstream_unaffected": 0, "skipped": 12, "total_scanned": 12},
    )


def _patch(valid: bool) -> Patch:
    return Patch(
        asset_urn=dataset_id("shop_prod.analytics.stg_orders"),
        file_path="demo-warehouse/models/staging/stg_orders.sql",
        before="select 1",
        after="select 2",
        unified_diff="",
        kind="dbt_sql",
        references=[
            ReferenceCheck(
                table="shop_prod.raw.orders",
                column="order_created_at",
                status="OK" if valid else "UNKNOWN_COLUMN",
                detail="test",
                source="live_catalog",
            )
        ],
        valid=valid,
        strategy="test",
    )


def test_completed_stages_come_from_real_artifacts() -> None:
    assert _completed_stages(RepairRun(id="r", status="failed", drift=_drift())) == ["detect"]
    assert _completed_stages(RepairRun(id="r", status="failed", drift=_drift(), impact=_impact(3))) == [
        "detect",
        "impact",
    ]
    no_op = RepairRun(id="r", status="succeeded", drift=_drift(), impact=_impact())
    assert _completed_stages(no_op) == ["detect", "impact", "codegen", "validate"]
    no_op.pr = PullRequestResult(
        mode="dry-run",
        url="",
        branch="repair/no-op",
        ok=True,
        state="no_changes_required",
    )
    assert _completed_stages(no_op) == ["detect", "impact", "codegen", "validate", "pr"]
    invalid = RepairRun(id="r", status="failed", drift=_drift(), impact=_impact(), patches=[_patch(False)])
    assert "codegen" in _completed_stages(invalid) and "validate" not in _completed_stages(invalid)
    failed_pr = RepairRun(
        id="r",
        status="failed",
        drift=_drift(),
        impact=_impact(),
        patches=[_patch(True)],
        pr=PullRequestResult(mode="live", url="", branch="b", ok=False, error="boom"),
    )
    assert "validate" in _completed_stages(failed_pr) and "pr" not in _completed_stages(failed_pr)


def test_dry_run_provider_writes_complete_payload(tmp_path: Path) -> None:
    request = PRRequest(
        branch=f"repair/{DRIFT_ID}",
        base="main",
        title="Repair timestamp rename",
        body_markdown="# Complete review body\n",
        files=[FileChange(path="models/stg_orders.sql", content="select 1\n")],
        commit_message="Repair schema drift",
    )
    result = DryRunPRProvider(tmp_path).open_pr(request)
    payload = json.loads((tmp_path / f"{DRIFT_ID}.payload.json").read_text(encoding="utf-8"))
    assert result.ok and result.mode == "dry-run"
    assert (tmp_path / f"{DRIFT_ID}.md").read_text(encoding="utf-8") == request.body_markdown
    assert payload["files"] == ["models/stg_orders.sql"] and payload["branch"] == request.branch


def test_fastapi_run_and_sse_stream_complete(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    async def fake_run_repair(
        run_id: str,
        drift_id: str,
        *,
        use_llm: bool,
        pr_mode: str,
        settings: Settings,
        on_event,
        run: RepairRun,
    ) -> RepairRun:
        del run_id, drift_id, use_llm, pr_mode, settings
        event = RunEvent(seq=1, phase="detect", title="Offline detection complete")
        run.events.append(event)
        on_event(event)
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        done = RunEvent(seq=2, phase="done", title="Offline run complete")
        run.events.append(done)
        on_event(done)
        return run

    monkeypatch.setattr(api_module, "run_repair", fake_run_repair)
    monkeypatch.setattr(api_module, "_live_drift_events", lambda _io, _settings: [_drift()])

    class FakeIO:
        def __init__(self, _settings: Settings) -> None:
            pass

        def preflight(self) -> dict[str, object]:
            return {"graph": "test"}

    monkeypatch.setattr(api_module, "FalkorDBIO", FakeIO)
    settings = Settings(_env_file=None, repo_root=tmp_path, groq_api_key="")  # type: ignore[arg-type]
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        assert len(client.get("/api/scenarios").json()) == 4
        unknown = client.post(
            "/api/runs",
            json={"drift_id": "<img src=x onerror=alert(1)>", "pr_mode": "dry-run", "use_llm": False},
        )
        assert unknown.status_code == 404
        started = client.post(
            "/api/runs",
            json={"drift_id": DRIFT_ID, "pr_mode": "dry-run", "use_llm": False},
        )
        run_id = started.json()["run_id"]
        with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            stream = "".join(response.iter_text())
        assert "data:" in stream and "event: done" in stream
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "succeeded"
        assert (tmp_path / ".relink" / "runs" / f"{run_id}.json").is_file()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/runs/{run_id}").json()["status"] == "succeeded"
