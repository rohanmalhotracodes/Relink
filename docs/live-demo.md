# Live GitHub demo

Relink is webhook-first. Nobody needs to open the website or click **Run demo**. GitHub starts the
investigation; the website is the durable evidence view linked from the bot comment.

## Recommended deployment: Railway

Use one Railway project with two services in the same environment:

1. **falkordb** — Docker image `falkordb/falkordb:latest`, no public domain, port `6379`, and a
   Railway volume mounted at `/var/lib/falkordb/data`.
2. **relink** — this repository, built from its root `Dockerfile`, with a public Railway domain and
   a volume mounted at `/app/.relink` so completed evidence runs survive deployments.

Keep both services in the same region. Run one Relink replica for this demo because webhook work is
currently executed in the web process.

Set these variables on the Relink service:

```dotenv
FALKORDB_HOST=falkordb.railway.internal
FALKORDB_PORT=6379
FALKORDB_GRAPH=relink_jaffle
WAREHOUSE_DIR=demo-jaffle
NAMESPACE_PREFIX=jaffle_shop.
REPO_ROOT=/app
GROQ_API_KEY=<Railway secret>
GROQ_MODEL=openai/gpt-oss-120b
GITHUB_REPOSITORY=rohanmalhotracodes/jaffle_shop_duckdb
GITHUB_APP_ID=<app id>
GITHUB_APP_PRIVATE_KEY=<complete PEM, stored as a Railway secret>
GITHUB_WEBHOOK_SECRET=<webhook secret>
GITHUB_AUTO_DEMO_SCENARIO=rename_jaffle_user_id
REPAIR_AGENT_PUBLIC_URL=https://<relink-service>.up.railway.app
```

Do not set `GITHUB_APP_PRIVATE_KEY_PATH` in the deployed service. That option is for local files;
`GITHUB_APP_PRIVATE_KEY` accepts the complete PEM as a secret environment variable. Never commit it.

After Railway assigns the domain:

1. Open GitHub → Settings → Developer settings → GitHub Apps → **Relink Schema Repair**.
2. Set the webhook URL to `https://<relink-service>.up.railway.app/api/github/webhook`.
3. Keep the existing webhook secret and JSON content type.
4. Confirm **Issues** and **Pull request** events are subscribed.
5. Confirm repository permissions are **Contents: read/write**, **Issues: read/write**, and
   **Pull requests: read/write**, then approve any pending installation permission request.
6. Open `https://<relink-service>.up.railway.app/api/health` and confirm `ok`,
   `falkordb_reachable`, `llm_available`, and `github_app_configured` are all `true`.

Smee is only for local development. Remove it from the production path.

## Issue to open

Title:

```text
Schema proposal: rename raw_orders.user_id to customer_user_id
```

Body:

```markdown
## Proposed warehouse contract change

Rename `raw_orders.user_id` to `customer_user_id` so the source field is explicit about the
identifier it contains.

Before implementation, trace the complete downstream blast radius. Identify direct references
that require repair, assets protected by a stable alias, unrelated assets, and the exact graph path
behind every PATCH or SKIP decision.
```

Opening it automatically triggers Relink. The App first posts a started comment, then a FalkorDB
decision table with the multi-hop paths.

## Pull request to open

Create a branch from `duckdb` and change only the first line of `seeds/raw_orders.csv`:

```diff
-id,user_id,order_date,status
+id,customer_user_id,order_date,status
```

Use this title:

```text
Rename raw_orders.user_id to customer_user_id
```

Use this body:

```markdown
## Schema change

Renames the upstream `raw_orders.user_id` contract to `customer_user_id`.

Relink should trace the FalkorDB lineage, repair only direct references, preserve the downstream
`customer_id` alias boundary, validate the candidate SQL, and attach the evidence here.

Closes #1
```

Opening the PR automatically triggers Relink. For this fixture it should:

- classify `stg_orders` as PATCH;
- classify `orders` and `customers` as SKIP because the `customer_id` alias protects them;
- classify `stg_customers` and `stg_payments` as unrelated;
- commit one validated change to `models/staging/stg_orders.sql` on the same PR branch;
- add the public evidence link to the PR description and post a final review comment.

Unrelated issues and PRs are ignored. The demo trigger requires both rename terms in the issue or
the actual seed-header diff in the pull request.

## Production limitations

The evidence pages are currently public to anyone who knows a run URL. Add GitHub login and
repository authorization before using Relink for private production repositories. For higher
availability, move webhook runs from in-process tasks to a durable queue and worker before scaling
the API beyond one replica.
