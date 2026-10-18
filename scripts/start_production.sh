#!/bin/sh
set -eu

python scripts/ensure_falkordb.py
exec uvicorn --app-dir src repair_agent.api.app:app --host 0.0.0.0 --port "${PORT:-8002}"
