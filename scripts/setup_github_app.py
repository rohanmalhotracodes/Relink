"""Register a local Relink GitHub App through GitHub's manifest flow.

This helper intentionally stores only the App ID, webhook secret, and generated
private key. OAuth client credentials returned by GitHub are discarded because
Relink authenticates as an installation, not as a user.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import secrets
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
KEY_PATH = REPO_ROOT / ".relink" / "github-app.private-key.pem"


def _update_env(values: dict[str, str]) -> None:
    """Update dotenv values without logging secret material."""

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(values)
    updated: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    if remaining:
        if updated and updated[-1]:
            updated.append("")
        updated.extend(f"{key}={value}" for key, value in remaining.items())
    ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _store_manifest_result(payload: dict[str, object]) -> tuple[str, str]:
    app_id = payload.get("id")
    slug = payload.get("slug")
    pem = payload.get("pem")
    webhook_secret = payload.get("webhook_secret")
    if not isinstance(app_id, int) or not all(isinstance(value, str) and value for value in (slug, pem, webhook_secret)):
        raise ValueError("GitHub returned an incomplete App manifest response.")

    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(str(pem), encoding="utf-8")
    KEY_PATH.chmod(0o600)
    _update_env(
        {
            "GITHUB_APP_ID": str(app_id),
            "GITHUB_APP_PRIVATE_KEY_PATH": str(KEY_PATH),
            "GITHUB_WEBHOOK_SECRET": str(webhook_secret),
        }
    )
    return str(slug), f"https://github.com/apps/{slug}/installations/new"


def _exchange_manifest_code(code: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions",
        data=b"",
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Relink-local-setup",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} · Relink</title>
  <style>
    body {{ margin: 0; background: #f4f8fc; color: #071f41; font: 16px/1.55 Inter, system-ui, sans-serif; }}
    main {{
      max-width: 680px; margin: 12vh auto; padding: 44px; background: white;
      border: 1px solid #cbdced; border-radius: 20px; box-shadow: 0 24px 70px rgba(7,31,65,.10);
    }}
    h1 {{ margin: 0 0 12px; font-size: 34px; letter-spacing: -.03em; }}
    p {{ color: #526a84; }}
    button, a.button {{
      display: inline-block; margin-top: 18px; border: 0; border-radius: 999px;
      padding: 14px 22px; background: #0a66c2; color: white; font-weight: 700;
      text-decoration: none; cursor: pointer;
    }}
    code {{ background: #eef5fb; padding: 2px 6px; border-radius: 5px; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>""".encode()


def build_handler(*, state: str, manifest: dict[str, object]):
    class SetupHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # Callback URLs contain a one-time credential. Never log request paths.
            print(f"GitHub App setup: HTTP {args[1] if len(args) > 1 else '-'}")

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                github_url = f"https://github.com/settings/apps/new?{urlencode({'state': state})}"
                encoded_manifest = html.escape(json.dumps(manifest), quote=True)
                body = _page(
                    "Create the Relink GitHub App",
                    f"""
                    <h1>Create Relink</h1>
                    <p>The manifest preconfigures the webhook, issue and pull-request
                    permissions, and subscribed events. GitHub will ask you to confirm
                    the globally unique App name.</p>
                    <form action="{github_url}" method="post">
                      <input type="hidden" name="manifest" value="{encoded_manifest}">
                      <button type="submit">Continue to GitHub</button>
                    </form>
                    """,
                )
                self._send(200, body)
                return

            if parsed.path != "/callback":
                self._send(404, _page("Not found", "<h1>Not found</h1>"))
                return

            query = parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            returned_state = query.get("state", [""])[0]
            if not code or not secrets.compare_digest(returned_state, state):
                self._send(
                    400,
                    _page(
                        "Setup rejected",
                        "<h1>Setup rejected</h1><p>The GitHub callback was missing a valid code or state.</p>",
                    ),
                )
                return
            try:
                payload = _exchange_manifest_code(code)
                slug, install_url = _store_manifest_result(payload)
            except (OSError, ValueError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                self._send(502, _page("Setup failed", f"<h1>Setup failed</h1><p>{html.escape(str(exc))}</p>"))
                return
            body = _page(
                "App created",
                f"""
                <h1>Relink is created</h1>
                <p>The App ID, generated webhook secret, and private key were stored locally.
                Install <code>{html.escape(slug)}</code> on only
                <code>rohanmalhotracodes/jaffle_shop_duckdb</code>, then restart the Relink API.</p>
                <a class="button" href="{html.escape(install_url)}">Install on the repository</a>
                """,
            )
            self._send(200, body)

    return SetupHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webhook-url", required=True, help="Public Smee or HTTPS tunnel webhook URL")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--name", default="Relink Rohan Schema Repair")
    args = parser.parse_args()

    state = secrets.token_urlsafe(32)
    callback_url = f"http://127.0.0.1:{args.port}/callback"
    manifest = {
        "name": args.name,
        "url": "https://github.com/rohanmalhotracodes/jaffle_shop_duckdb",
        "description": "FalkorDB-backed schema-drift repair agent with explainable issue and pull-request evidence.",
        "hook_attributes": {"url": args.webhook_url, "active": True},
        "redirect_url": callback_url,
        "public": False,
        "default_permissions": {"contents": "write", "issues": "write", "pull_requests": "write"},
        "default_events": ["issues", "pull_request"],
        "request_oauth_on_install": False,
    }
    server = ThreadingHTTPServer(("127.0.0.1", args.port), build_handler(state=state, manifest=manifest))
    print(f"Open http://127.0.0.1:{args.port} to approve the preconfigured GitHub App.")
    print("The helper will store credentials in .env and .relink without printing them.")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
