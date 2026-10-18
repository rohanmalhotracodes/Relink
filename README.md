<p align="center">
  <img src="web/public/relink-logo.png" alt="Relink" width="420" />
</p>

# Relink

Relink detects upstream column renames, retypes, and drops; investigates their
multi-hop impact in FalkorDB; generates deterministic AST-based SQL/Airflow repairs;
validates every candidate reference against the live graph; and prepares a reviewable pull
request. FalkorDB is authoritative for schemas, column lineage, impact classification,
validation, incidents, and repair-run state.

The UI preserves the original control-room workflow while making Cypher-derived paths,
alias insulation, skipped assets, validation evidence, proposed diffs, and graph write-back
visible. Pull requests are dry-run by default.

## Quick start

Prerequisites: Docker, Python 3.11, [uv](https://docs.astral.sh/uv/), and Node 18+.

```bash
cp .env.example .env
make graph-up
make setup
make seed
make verify
make demo
```

Open <http://localhost:3002>. FalkorDB's browser is exposed by the included Compose file at
<http://localhost:3001> (override it with `FALKORDB_BROWSER_PORT`).

CLI workflow:

```bash
uv run relink simulate rename_order_placed_at
uv run relink detect
uv run relink impact rename-orders-order_placed_at
uv run relink run rename-orders-order_placed_at --no-llm
uv run relink simulate --revert
```

Supported scenarios are `rename_order_placed_at`, `retype_gross_amount`, and
`drop_marketing_opt_in`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FALKORDB_HOST` | `localhost` | FalkorDB host |
| `FALKORDB_PORT` | `6379` | FalkorDB Redis-protocol port |
| `FALKORDB_BROWSER_PORT` | `3001` | Host port for the optional FalkorDB browser |
| `FALKORDB_GRAPH` | `schema_repair` | Dedicated graph name |
| `WAREHOUSE_DIR` | `demo-warehouse` | Warehouse metadata and local repair fixture; the example env selects `demo-jaffle` |
| `NAMESPACE_PREFIX` | `shop_prod.` | Dataset namespace scanned for drift; the Jaffle demo uses `jaffle_shop.` |
| `FALKORDB_USERNAME` / `FALKORDB_PASSWORD` | empty | Optional authentication |
| `FALKORDB_SSL` | `false` | TLS connection flag |
| `GROQ_API_KEY` | empty | The only Groq credential; enables MCP tool selection, structured action planning, and prose, and is never printed or stored |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Optional override for the Groq model used for dynamic tool selection, explanations, and PR prose |
| `GROQ_MAX_TOOL_STEPS` | `8` | Maximum dynamic MCP planning rounds before safeguards take over |
| `REPAIR_AGENT_PR_MODE` | `dry-run` | `live` must be explicitly requested |
| `GITHUB_REPOSITORY` | `rohanmalhotracodes/jaffle_shop_duckdb` in the example env | GitHub App allowlist and local simulation label |
| `GITHUB_ISSUE_NUMBER` | empty | Optional issue or PR number to receive the repair summary |
| `GITHUB_COMMENT_MODE` | `dry-run` | Comment delivery gate; posting requires this and the run's PR mode to both be `live` |
| `REPAIR_AGENT_PUBLIC_URL` | `http://localhost:3002` | Base URL used in the comment's deep link to run evidence |
| `GITHUB_APP_ID` | empty | Optional GitHub App ID; kept server-side |
| `GITHUB_APP_PRIVATE_KEY_PATH` | empty | Absolute path to the GitHub App PEM private key; never commit the key |
| `GITHUB_WEBHOOK_SECRET` | empty | HMAC secret used to verify `X-Hub-Signature-256` |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST API base URL |
| `GITHUB_AUTO_DEMO_SCENARIO` | `rename_jaffle_user_id` in the example env | Demo-only drift applied by the first signed issue/PR webhook when the graph is clean |

Without a Groq key, the full graph reasoning, repair, validation, report, dry-run PR, and
graph-recording workflow still runs with deterministic prose. Groq receives drift facts,
structured graph-tool results, and summarized patch strategies, but never patch contents;
it cannot author or alter code.

With a key, Groq discovers and dynamically selects read-only FalkorDB tools through a real
stdio MCP session. It uses each graph result to choose its next call, then submits structured
PATCH/SKIP actions for every asset. Deterministic graph policy must approve every action before
that plan can drive the constrained AST executor. Validation, PR safety, and graph write-back
remain hard gates.

## Graph and reasoning

See [docs/architecture.md](docs/architecture.md) for the node/relationship model, stable
IDs, ingestion contract, Cypher reasoning rules, and safety boundaries.

The MCP server can be started independently over stdio:

```bash
uv run falkordb-mcp
```

It exposes:

- `inspect_schema`
- `trace_column_impact`
- `explore_lineage_bfs` (native FalkorDB `algo.bfs`)
- `explain_lineage_path`
- `classify_affected_asset`
- `assess_repair_candidates`
- `validate_candidate_repair`
- `record_repair_result`

## Safety model

Code patches are produced only by the existing `sqlglot`/structured YAML/Airflow editors.
The validator resolves SQL references against FalkorDB's active schema, with projected
schemas used only for another model patched in the same candidate set. A single unresolved
reference blocks review. Live PR creation additionally requires `--pr-mode live` and an
authenticated `gh` CLI. This migrated workspace intentionally has no inherited Git history;
initialize it as a new repository and add an `origin` remote before using live mode. Dry-run
mode needs neither Git history nor GitHub access.

Every local run creates two separate artifacts in `.relink/github_comments/`: the impact
assessment that would be posted to an issue and the validated repair summary that would be
posted to a pull request. The Reviewer Handoff screen renders both conversations; local mode
does not contact GitHub.

### Optional GitHub App for the demo fork

The example environment targets
[`rohanmalhotracodes/jaffle_shop_duckdb`](https://github.com/rohanmalhotracodes/jaffle_shop_duckdb).
To receive real events while Relink runs locally:

The shortest setup uses GitHub's App Manifest flow. Create a Smee channel, then run:

```bash
uv run python scripts/setup_github_app.py --webhook-url https://smee.io/YOUR_CHANNEL
```

Open `http://127.0.0.1:8787`, approve the prefilled App, and install it only on the demo fork.
The helper stores the App ID, GitHub-generated webhook secret, and PEM under ignored local paths;
it never prints them. Forward the Smee channel to `http://127.0.0.1:8002/api/github/webhook`.

The equivalent manual setup is:

1. Register a private GitHub App and install it only on that fork.
2. Give it **Contents: read/write**, **Issues: read/write**, and **Pull requests: read/write** repository permissions.
3. Subscribe to **Issues** and **Pull request** events.
4. Expose the local API through an HTTPS tunnel and set the webhook URL to
   `https://YOUR-TUNNEL/api/github/webhook`.
5. Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, and `GITHUB_WEBHOOK_SECRET` in `.env`.
6. Set `REPAIR_AGENT_PUBLIC_URL` to the public URL that serves the Relink UI, so links in
   GitHub open the exact run instead of `localhost` on the reviewer's machine.

The webhook verifies GitHub's SHA-256 signature over the raw request, rejects repositories
outside the configured allowlist, ignores duplicate delivery IDs, authenticates with a
short-lived installation token, and reacts to newly opened issues plus opened, reopened, and
synchronized pull requests. In the supplied demo configuration, the first signed repository
event applies the controlled Jaffle rename scenario if the graph is clean. An issue receives a
graph-backed impact assessment. A pull request receives the same evidence, an idempotent deep link,
and—only after graph validation—one atomic compare-before-write repair commit on its existing source
branch. Relink ignores the resulting `synchronize` event from its own commit to prevent webhook loops.

### Live GitHub-native demo

For the production deployment layout, exact environment variables, GitHub App configuration,
and copy-paste issue/PR scripts, see [docs/live-demo.md](docs/live-demo.md).

The bundled Jaffle fixture mirrors the relevant paths in the configured fork. The showcase flow is:

1. Open an issue proposing `raw_orders.user_id` → `customer_user_id`. Relink comments with the
   multi-hop FalkorDB path, the direct patch target, alias-protected skips, and unrelated skips.
2. Open a pull request that makes that rename in `seeds/raw_orders.csv`.
3. Relink posts an investigation-started comment, lets Groq select FalkorDB MCP evidence tools,
   changes only `models/staging/stg_orders.sql`, validates every resulting column reference against
   FalkorDB, and commits the repair to the pull-request branch.
4. The final PR comment contains the PATCH/SKIP decision table, validation counts, and a link to the
   full local evidence view. `orders.sql` and `customers.sql` remain unchanged because `customer_id`
   is a stable alias boundary.

## Development

```bash
make test
make lint
cd web && npm run build
```

The default test suite uses a recording FalkorDB driver to assert the exact Cypher contract.
For a live smoke test, run `make graph-up seed verify` first.

## Current limitations

- The demo warehouse has no live Snowflake connection; dbt validation uses DuckDB when the
  optional demo dependencies are installed.
- Column lineage is explicit metadata in `graph_metadata.yml` because no compiled dbt
  `manifest.json` is committed. The ingester still parses dbt YAML/SQL, the Airflow DAG,
  and `code_map.yml`, and rejects mismatched task/file mappings.
- Airflow SQL repair is limited to configured module-level SQL constants.
- Live webhook repair currently supports the configured Jaffle demo contract whose repository paths
  are mapped by `demo-jaffle/code_map.yml`. Cloning and ingesting arbitrary repository revisions on
  demand is not implemented yet.
