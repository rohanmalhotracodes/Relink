"""FastAPI backend and live SSE transport for repair runs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from repair_agent.agent.runner import run_repair
from repair_agent.config import Settings, get_settings
from repair_agent.drift.detect import detect_drift
from repair_agent.drift.snapshot import SchemaSnapshot
from repair_agent.falkordb_io.client import FalkorDBIO
from repair_agent.falkordb_io.ids import dataset_name
from repair_agent.github_app import (
    GitHubAppClient,
    append_investigation_link,
    matches_demo_trigger,
    verify_webhook_signature,
)
from repair_agent.models import DriftEvent, RepairRun, RunEvent
from repair_agent.pr.github_comment import render_issue_comment, render_pr_comment

LOGGER = logging.getLogger(__name__)
SCENARIOS = {
    "rename_order_placed_at": {
        "drift_id": "rename-orders-order_placed_at",
        "kind": "RENAME",
        "title": "Rename orders.order_placed_at",
        "description": "Rename the source timestamp to order_created_at and repair exact downstream references.",
    },
    "retype_gross_amount": {
        "drift_id": "retype-orders-gross_amount",
        "kind": "RETYPE",
        "title": "Retype orders.gross_amount",
        "description": "Change the source amount to VARCHAR and preserve downstream numeric semantics with casts.",
    },
    "drop_marketing_opt_in": {
        "drift_id": "drop-customers-marketing_opt_in",
        "kind": "DROP",
        "title": "Drop customers.marketing_opt_in",
        "description": "Remove the source field and produce an explicit deprecation path rather than a silent delete.",
    },
    "rename_jaffle_user_id": {
        "drift_id": "rename-raw_orders-user_id",
        "kind": "RENAME",
        "title": "Rename raw_orders.user_id",
        "description": "Repair the direct staging reference while preserving the customer_id alias boundary.",
    },
}


class SPAStaticFiles(StaticFiles):
    """Serve the built React app and preserve client-side deep links."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not path.startswith("api/"):
            return await super().get_response("index.html", scope)
        return response


class StartRunRequest(BaseModel):
    """Request body for one asynchronous repair run."""

    drift_id: str
    pr_mode: Literal["dry-run", "live"] = "dry-run"
    use_llm: bool = True
    github_repository: str = Field(default="", max_length=200)
    github_issue_number: int | None = Field(default=None, ge=1)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated API application, useful for production and TestClient."""

    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        _load_persisted_runs(application, active_settings)
        yield

    application = FastAPI(title="Relink", version="0.1.0", lifespan=lifespan)
    application.state.runs = {}
    application.state.queues = {}
    application.state.tasks = {}
    application.state.github_deliveries = set()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3002", "http://127.0.0.1:3002"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    async def health() -> dict[str, object]:
        degradations: list[str] = []
        reachable = True
        try:
            await asyncio.to_thread(FalkorDBIO(active_settings).preflight)
        except Exception as exc:
            reachable = False
            degradations.append(str(exc))
        if not active_settings.groq_api_key.strip():
            degradations.append("GROQ_API_KEY is absent; runs use deterministic mode with templated prose.")
        return {
            "ok": reachable,
            "falkordb_reachable": reachable,
            "graph": active_settings.falkordb_graph,
            "llm_available": bool(active_settings.groq_api_key.strip()),
            "github_repository": active_settings.github_repository,
            "github_comment_mode": active_settings.github_comment_mode,
            "github_app_configured": bool(
                active_settings.github_app_id.strip()
                and (active_settings.github_app_private_key_path or active_settings.github_app_private_key)
                and active_settings.github_webhook_secret.strip()
            ),
            "public_url": active_settings.repair_agent_public_url,
            "degradations": degradations,
        }

    @application.post("/api/github/webhook", status_code=202)
    async def github_webhook(request: Request) -> dict[str, object]:
        """Accept signed issue/PR events and run the local graph workflow for the configured fork."""

        if not (
            active_settings.github_app_id.strip()
            and (active_settings.github_app_private_key_path or active_settings.github_app_private_key)
            and active_settings.github_webhook_secret.strip()
        ):
            raise HTTPException(status_code=503, detail="GitHub App credentials are not configured.")
        raw = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(raw, signature, active_settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature.")
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if delivery_id and delivery_id in application.state.github_deliveries:
            return {"ok": True, "duplicate": True, "delivery_id": delivery_id}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Webhook payload is not valid JSON.") from exc
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            if delivery_id:
                application.state.github_deliveries.add(delivery_id)
            return {"ok": True, "event": "ping"}

        repository = str(payload.get("repository", {}).get("full_name", ""))
        if active_settings.github_repository and repository.casefold() != active_settings.github_repository.casefold():
            raise HTTPException(status_code=403, detail="Webhook repository is not the configured Relink repository.")
        installation_id = payload.get("installation", {}).get("id")
        if not isinstance(installation_id, int):
            raise HTTPException(status_code=400, detail="Webhook payload has no GitHub App installation ID.")

        action = str(payload.get("action", ""))
        supported = (event == "issues" and action == "opened") or (
            event == "pull_request" and action in {"opened", "reopened", "synchronize"}
        )
        if not supported:
            return {"ok": True, "ignored": True, "event": event, "action": action}
        item = payload.get("pull_request") if event == "pull_request" else payload.get("issue")
        if not isinstance(item, dict) or not isinstance(item.get("number"), int):
            raise HTTPException(status_code=400, detail="Webhook payload has no issue or pull-request number.")
        number = int(item["number"])
        client = GitHubAppClient(active_settings, installation_id)
        head = item.get("head", {}) if event == "pull_request" else {}
        head_repository = str(head.get("repo", {}).get("full_name", "")) if isinstance(head, dict) else ""
        head_ref = str(head.get("ref", "")) if isinstance(head, dict) else ""
        if event == "pull_request" and action == "synchronize":
            head_sha = str(head.get("sha", "")) if isinstance(head, dict) else ""
            if head_repository and head_sha:
                try:
                    commit_message = await client.get_commit_message(head_repository, head_sha)
                except Exception:
                    LOGGER.exception("Could not inspect synchronize commit %s in %s", head_sha, head_repository)
                else:
                    if commit_message.startswith("Repair schema drift "):
                        if delivery_id:
                            application.state.github_deliveries.add(delivery_id)
                        return {
                            "ok": True,
                            "ignored": True,
                            "event": event,
                            "action": action,
                            "reason": "relink_repair_commit",
                        }

        if active_settings.github_auto_demo_scenario:
            changed_files: list[dict[str, object]] = []
            if event == "pull_request":
                try:
                    changed_files = await client.list_pull_request_files(repository, number)
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"Could not inspect pull-request files: {exc}") from exc
            if not matches_demo_trigger(active_settings.github_auto_demo_scenario, event, item, changed_files):
                if delivery_id:
                    application.state.github_deliveries.add(delivery_id)
                return {
                    "ok": True,
                    "ignored": True,
                    "event": event,
                    "action": action,
                    "reason": "unsupported_demo_change",
                }

        try:
            active_events = await asyncio.to_thread(
                _live_drift_events,
                FalkorDBIO(active_settings),
                active_settings,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not query FalkorDB for webhook impact: {exc}") from exc
        if not active_events and active_settings.github_auto_demo_scenario:
            scenario = active_settings.github_auto_demo_scenario
            if scenario not in SCENARIOS:
                raise HTTPException(status_code=500, detail=f"Unknown GITHUB_AUTO_DEMO_SCENARIO {scenario!r}.")
            applied = await _run_script(active_settings, "simulate_drift.py", [scenario])
            if applied.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not apply the webhook demo drift: {applied.stderr.strip() or applied.stdout.strip()}",
                )
            active_events = await asyncio.to_thread(
                _live_drift_events,
                FalkorDBIO(active_settings),
                active_settings,
            )
        if not active_events:
            website = f"{active_settings.repair_agent_public_url.rstrip('/')}/demo"
            await client.create_comment(
                repository,
                number,
                "## Relink checked the graph\n\nNo active schema drift is recorded in FalkorDB, so no repair was proposed. "
                f"[Open the Relink workspace]({website}) to ingest or simulate a schema change.",
            )
            if event == "pull_request":
                await client.update_pull_request_body(
                    repository,
                    number,
                    append_investigation_link(str(item.get("body") or ""), website),
                )
            if delivery_id:
                application.state.github_deliveries.add(delivery_id)
            return {"ok": True, "started": False, "reason": "no_active_drift"}

        repair_run = RepairRun(id=f"run-{uuid4().hex}")
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        application.state.runs[repair_run.id] = repair_run
        application.state.queues[repair_run.id] = queue
        run_url = f"{active_settings.repair_agent_public_url.rstrip('/')}/demo/pr?run={repair_run.id}"
        await client.create_comment(
            repository,
            number,
            "## Relink investigation started\n\n"
            "FalkorDB has an active schema drift. Relink is tracing its multi-hop impact, "
            f"validating guarded repairs, and preparing evidence. [Follow the live run]({run_url}).",
        )
        if event == "pull_request":
            await client.update_pull_request_body(
                repository,
                number,
                append_investigation_link(str(item.get("body") or ""), run_url),
            )

        loop = asyncio.get_running_loop()

        def emit(run_event: RunEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, run_event)

        async def execute() -> None:
            try:
                await run_repair(
                    repair_run.id,
                    active_events[0].id,
                    use_llm=bool(active_settings.groq_api_key.strip()),
                    pr_mode="dry-run",
                    settings=active_settings,
                    on_event=emit,
                    run=repair_run,
                    github_repository=repository,
                    github_issue_number=number,
                    github_app_client=client if event == "pull_request" else None,
                    github_pull_request_number=number if event == "pull_request" else None,
                    github_head_repository=head_repository,
                    github_head_ref=head_ref,
                )
                comment = (
                    render_pr_comment(repair_run, active_settings.repair_agent_public_url)
                    if event == "pull_request"
                    else render_issue_comment(repair_run, active_settings.repair_agent_public_url)
                )
                await client.create_comment(repository, number, comment)
            except Exception:
                LOGGER.exception("GitHub App investigation failed for %s#%s", repository, number)
            finally:
                _persist_run(repair_run, active_settings)
                application.state.tasks.pop(repair_run.id, None)

        task = asyncio.create_task(execute(), name=f"relink-github-{repair_run.id}")
        application.state.tasks[repair_run.id] = task
        if delivery_id:
            application.state.github_deliveries.add(delivery_id)
        return {"ok": True, "started": True, "run_id": repair_run.id, "run_url": run_url}

    @application.get("/api/catalog")
    async def catalog() -> list[dict[str, object]]:
        io = FalkorDBIO(active_settings)

        def read() -> list[dict[str, object]]:
            datasets = []
            baseline = SchemaSnapshot.load(_snapshot_path(active_settings))
            for urn in baseline.dataset_urns():
                schema = io.get_schema(urn, skip_cache=True)
                name = dataset_name(urn)
                datasets.append(
                    {
                        "urn": urn,
                        "name": name,
                        "subtypes": ["Table" if ".raw." in name else "Model"],
                        "schema": schema.model_dump(mode="json"),
                    }
                )
            return datasets

        try:
            return await asyncio.to_thread(read)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not read the ShopFlow catalog: {exc}") from exc

    @application.get("/api/catalog/{urn:path}/schema")
    async def catalog_schema(urn: str) -> dict[str, object]:
        try:
            schema = await asyncio.to_thread(FalkorDBIO(active_settings).get_schema, urn, skip_cache=True)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not read schema for {urn}: {exc}") from exc
        if not schema.columns:
            raise HTTPException(status_code=404, detail=f"No active FalkorDB schema exists for {urn}.")
        return schema.model_dump(mode="json")

    @application.get("/api/scenarios")
    async def scenarios() -> list[dict[str, str]]:
        return [{"name": name, **details} for name, details in SCENARIOS.items()]

    @application.post("/api/scenarios/{name}/apply")
    async def apply_scenario(name: str, revert: bool = Query(default=False)) -> dict[str, object]:
        if name not in SCENARIOS:
            raise HTTPException(status_code=404, detail=f"Unknown scenario {name!r}; choose one of {sorted(SCENARIOS)}.")
        arguments = ["--revert"] if revert else [name]
        completed = await _run_script(active_settings, "simulate_drift.py", arguments)
        if completed.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Drift simulation failed: {completed.stderr.strip() or completed.stdout.strip()}",
            )
        return {"ok": True, "scenario": name, "reverted": revert, "detail": completed.stdout.strip()}

    @application.get("/api/drift")
    async def drift_events() -> list[dict[str, object]]:
        io = FalkorDBIO(active_settings)

        try:
            events = await asyncio.to_thread(_live_drift_events, io, active_settings)
            return [event.model_dump(mode="json") for event in events]
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not detect live drift: {exc}") from exc

    @application.post("/api/runs", status_code=202)
    async def start_run(request_body: StartRunRequest) -> dict[str, str]:
        try:
            active_events = await asyncio.to_thread(
                _live_drift_events,
                FalkorDBIO(active_settings),
                active_settings,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not validate the live drift request: {exc}") from exc
        active_ids = [event.id for event in active_events]
        if request_body.drift_id not in active_ids:
            available = ", ".join(active_ids) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"Unknown or inactive drift ID {request_body.drift_id!r}. Active drift IDs: {available}.",
            )
        run_id = f"run-{uuid4().hex}"
        repair_run = RepairRun(id=run_id)
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        application.state.runs[run_id] = repair_run
        application.state.queues[run_id] = queue
        loop = asyncio.get_running_loop()

        def emit(event: RunEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        task = asyncio.create_task(
            _execute_run(
                application,
                active_settings,
                repair_run,
                request_body,
                emit,
            ),
            name=f"relink-{run_id}",
        )
        application.state.tasks[run_id] = task
        return {"run_id": run_id}

    @application.get("/api/runs")
    async def list_runs() -> list[dict[str, object]]:
        runs = sorted(application.state.runs.values(), key=lambda item: item.started_at, reverse=True)
        return [run.model_dump(mode="json") for run in runs]

    @application.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, object]:
        repair_run = _require_run(application, run_id)
        return repair_run.model_dump(mode="json")

    @application.get("/api/runs/{run_id}/graph")
    async def get_graph(run_id: str) -> dict[str, object]:
        repair_run = _require_run(application, run_id)
        if repair_run.impact is None:
            raise HTTPException(status_code=409, detail="Impact analysis has not completed for this run yet.")
        return repair_run.impact.graph.model_dump(mode="json")

    @application.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, request: Request) -> StreamingResponse:
        repair_run = _require_run(application, run_id)
        queue = application.state.queues.setdefault(run_id, asyncio.Queue())

        async def frames() -> AsyncIterator[str]:
            last_seq = 0
            try:
                for event in list(repair_run.events):
                    last_seq = max(last_seq, event.seq)
                    yield f"data: {event.model_dump_json()}\n\n"
                while repair_run.status == "running":
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event.seq <= last_seq:
                        continue
                    last_seq = event.seq
                    yield f"data: {event.model_dump_json()}\n\n"
                for event in repair_run.events:
                    if event.seq > last_seq:
                        last_seq = event.seq
                        yield f"data: {event.model_dump_json()}\n\n"
                yield f"event: done\ndata: {json.dumps({'run_id': run_id, 'status': repair_run.status})}\n\n"
            except asyncio.CancelledError:
                LOGGER.info("SSE client disconnected from run %s", run_id)
                return

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/api/reset")
    async def reset_demo() -> dict[str, object]:
        applied = active_settings.warehouse_path / ".relink" / "applied_drift.json"
        outputs: list[str] = []
        if applied.exists():
            reverted = await _run_script(active_settings, "simulate_drift.py", ["--revert"])
            if reverted.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Could not revert active drift: {reverted.stderr.strip()}")
            outputs.append(reverted.stdout.strip())
        seeded = await _run_script(active_settings, "seed_falkordb.py", ["--reset", "--verify"])
        if seeded.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Could not re-seed the demo catalog: {seeded.stderr.strip()}")
        outputs.append(seeded.stdout.strip())
        return {"ok": True, "detail": "\n".join(outputs)}

    web_dist = active_settings.repo_root / "web" / "dist"
    if web_dist.is_dir():
        application.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="web")

    return application


async def _execute_run(
    application: FastAPI,
    settings: Settings,
    repair_run: RepairRun,
    request: StartRunRequest,
    emit: Callable[[RunEvent], None],
) -> None:
    try:
        optional_github: dict[str, object] = {}
        if request.github_repository:
            optional_github["github_repository"] = request.github_repository
        if request.github_issue_number is not None:
            optional_github["github_issue_number"] = request.github_issue_number
        await run_repair(
            repair_run.id,
            request.drift_id,
            use_llm=request.use_llm,
            pr_mode=request.pr_mode,
            settings=settings,
            on_event=emit,
            run=repair_run,
            **optional_github,
        )
    finally:
        _persist_run(repair_run, settings)
        application.state.tasks.pop(repair_run.id, None)


async def _run_script(settings: Settings, script: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["FALKORDB_HOST"] = settings.falkordb_host
    environment["FALKORDB_PORT"] = str(settings.falkordb_port)
    environment["FALKORDB_GRAPH"] = settings.falkordb_graph
    command = [sys.executable, str(settings.repo_root / "scripts" / script), *arguments]
    return await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=settings.repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_run(application: FastAPI, run_id: str) -> RepairRun:
    repair_run = application.state.runs.get(run_id)
    if repair_run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} was not found.")
    return repair_run


def _live_drift_events(graph_io: FalkorDBIO, settings: Settings) -> list[DriftEvent]:
    """Read the one live drift source shared by the API listing and run gate."""

    graph_io.preflight()
    baseline = SchemaSnapshot.load(_snapshot_path(settings))
    live = SchemaSnapshot.capture(
        graph_io,
        settings.namespace_prefix,
        known_urns=baseline.dataset_urns(),
    )
    return detect_drift(baseline, live)


def _snapshot_path(settings: Settings) -> Path:
    return settings.warehouse_path / ".relink" / "snapshot.json"


def _run_dir(settings: Settings) -> Path:
    return settings.repo_root / ".relink" / "runs"


def _persist_run(repair_run: RepairRun, settings: Settings) -> None:
    if repair_run.finished_at is None:
        return
    directory = _run_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath(f"{repair_run.id}.json").write_text(
        repair_run.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _load_persisted_runs(application: FastAPI, settings: Settings) -> None:
    directory = _run_dir(settings)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            repair_run = RepairRun.model_validate_json(path.read_text(encoding="utf-8"))
            application.state.runs.setdefault(repair_run.id, repair_run)
            application.state.queues.setdefault(repair_run.id, asyncio.Queue())
        except Exception as exc:
            LOGGER.warning("Ignoring unreadable persisted run %s: %s", path, exc)


app = create_app()
