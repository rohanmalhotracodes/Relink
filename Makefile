UV_CACHE_DIR ?= $(HOME)/.cache/relink
UV_TOOL_DIR ?= $(HOME)/.cache/relink/tools
export UV_TOOL_DIR
RUN := env -u VIRTUAL_ENV UV_CACHE_DIR=$(UV_CACHE_DIR) UV_TOOL_DIR=$(UV_TOOL_DIR) uv run
SCENARIO ?= rename_order_placed_at
API_PORT ?= 8002
WEB_PORT ?= 3002

.PHONY: setup graph-up graph-down preflight seed verify simulate test lint backend frontend demo stop

setup:
	@command -v uv >/dev/null 2>&1 || { echo "ERROR: install uv first."; exit 1; }
	@env -u VIRTUAL_ENV UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync
	@cd web && npm install --silent

graph-up:
	@docker compose up -d falkordb

graph-down:
	@docker compose down

preflight:
	@$(RUN) python -c "from repair_agent.falkordb_io.client import FalkorDBIO; print(FalkorDBIO().preflight())"

seed: preflight
	@$(RUN) python scripts/seed_falkordb.py

verify: preflight
	@$(RUN) python scripts/seed_falkordb.py --verify

simulate:
	@$(RUN) python scripts/simulate_drift.py $(SCENARIO)

test:
	@$(RUN) pytest tests/ -q

lint:
	@$(RUN) ruff check src/ scripts/ tests/

backend: preflight
	@$(RUN) uvicorn --app-dir src repair_agent.api.app:app --port $(API_PORT) --host 127.0.0.1

frontend:
	@cd web && npm run dev

demo: preflight
	@mkdir -p .relink
	@$(RUN) uvicorn --app-dir src repair_agent.api.app:app --port $(API_PORT) --host 127.0.0.1 > .relink/api.log 2>&1 & echo $$! > .relink/api.pid
	@trap '$(MAKE) -C $(CURDIR) stop' EXIT; cd web && npm run dev

stop:
	@if test -f .relink/api.pid; then kill $$(cat .relink/api.pid) 2>/dev/null || true; fi
