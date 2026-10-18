from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path

from repair_agent.config import Settings
from repair_agent.github_app import (
    RELINK_BODY_MARKER,
    GitHubAppClient,
    append_investigation_link,
    matches_demo_trigger,
    verify_webhook_signature,
)
from repair_agent.models import FileChange


def test_webhook_signature_uses_raw_payload_and_sha256() -> None:
    payload = b'{"action":"opened","unicode":"\xe2\x9c\x93"}'
    secret = "test-secret"
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, signature, secret)
    assert not verify_webhook_signature(payload + b" ", signature, secret)
    assert not verify_webhook_signature(payload, "sha1=bad", secret)


def test_pr_description_link_is_idempotent_and_replaceable() -> None:
    first = append_investigation_link("Human-authored description", "https://relink.test/demo/pr?run=one")
    second = append_investigation_link(first, "https://relink.test/demo/pr?run=two")

    assert second.count(RELINK_BODY_MARKER) == 1
    assert "Human-authored description" in second
    assert "run=one" not in second
    assert "run=two" in second


def test_jaffle_demo_trigger_requires_the_real_contract_change() -> None:
    issue = {"title": "Rename raw_orders.user_id", "body": "Use customer_user_id instead."}
    files = [
        {
            "filename": "seeds/raw_orders.csv",
            "patch": "-id,user_id,order_date,status\n+id,customer_user_id,order_date,status",
        }
    ]

    assert matches_demo_trigger("rename_jaffle_user_id", "issues", issue)
    assert matches_demo_trigger("rename_jaffle_user_id", "pull_request", {}, files)
    assert not matches_demo_trigger("rename_jaffle_user_id", "issues", {"title": "Update docs", "body": ""})
    assert not matches_demo_trigger(
        "rename_jaffle_user_id",
        "pull_request",
        {},
        [{"filename": "README.md", "patch": "+hello"}],
    )


def test_github_app_creates_one_atomic_compare_before_write_commit(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    key = tmp_path / "app.pem"
    key.write_text("unused in mocked request", encoding="utf-8")
    client = GitHubAppClient(
        Settings(_env_file=None, github_app_id="123", github_app_private_key_path=key), 7  # type: ignore[arg-type]
    )
    calls: list[tuple[str, str, dict | None, dict | None]] = []

    async def request(method: str, path: str, *, json=None, params=None):  # type: ignore[no-untyped-def]
        calls.append((method, path, json, params))
        if path.endswith("/git/ref/heads/demo/schema-break") and method == "GET":
            return {"object": {"sha": "parent"}}
        if path.endswith("/git/commits/parent"):
            return {"tree": {"sha": "base-tree"}}
        if "/contents/" in path:
            import base64

            return {"content": base64.b64encode(b"select user_id as customer_id\n").decode()}
        if path.endswith("/git/blobs"):
            return {"sha": "blob"}
        if path.endswith("/git/trees"):
            return {"sha": "new-tree"}
        if path.endswith("/git/commits"):
            return {"sha": "new-commit", "html_url": "https://github.test/commit/new-commit"}
        if method == "PATCH":
            return {"ref": "heads/demo/schema-break"}
        raise AssertionError((method, path))

    monkeypatch.setattr(client, "_request", request)
    url = asyncio.run(
        client.commit_files(
            "acme/warehouse",
            "demo/schema-break",
            [
                FileChange(
                    path="models/staging/stg_orders.sql",
                    previous_content="select user_id as customer_id\n",
                    content="select customer_user_id as customer_id\n",
                )
            ],
            "Repair schema drift",
        )
    )

    assert url.endswith("/new-commit")
    tree_call = next(call for call in calls if call[1].endswith("/git/trees"))
    assert tree_call[2] == {
        "base_tree": "base-tree",
        "tree": [
            {
                "path": "models/staging/stg_orders.sql",
                "mode": "100644",
                "type": "blob",
                "sha": "blob",
            }
        ],
    }
    assert calls[-1][0] == "PATCH" and calls[-1][2] == {"sha": "new-commit", "force": False}
