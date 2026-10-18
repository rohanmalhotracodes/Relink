"""Minimal, server-side GitHub App authentication and delivery client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import PurePosixPath
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from repair_agent.config import Settings
from repair_agent.models import FileChange

GITHUB_API_VERSION = "2026-03-10"
RELINK_BODY_MARKER = "<!-- relink-investigation -->"


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub's X-Hub-Signature-256 without decoding or changing the payload."""

    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def append_investigation_link(body: str | None, url: str) -> str:
    """Add or replace the one Relink block in a pull-request description."""

    original = body or ""
    block = (
        f"{RELINK_BODY_MARKER}\n"
        "---\n"
        f"### Relink investigation\n\n[Open graph evidence, validation, and proposed repairs]({url}).\n"
    )
    if RELINK_BODY_MARKER in original:
        return original.split(RELINK_BODY_MARKER, 1)[0].rstrip() + "\n\n" + block
    return (original.rstrip() + "\n\n" + block).lstrip()


class GitHubAppClient:
    """Authenticate as one installation and call only the APIs Relink needs."""

    def __init__(self, settings: Settings, installation_id: int) -> None:
        if not settings.github_app_id.strip():
            raise ValueError("GITHUB_APP_ID is not configured.")
        if settings.github_app_private_key_path is None and settings.github_app_private_key is None:
            raise ValueError("GITHUB_APP_PRIVATE_KEY_PATH or GITHUB_APP_PRIVATE_KEY is not configured.")
        self.app_id = settings.github_app_id.strip()
        self.private_key_path = (
            settings.github_app_private_key_path.expanduser().resolve()
            if settings.github_app_private_key_path is not None
            else None
        )
        self.private_key = settings.github_app_private_key
        self.installation_id = installation_id
        self.api_url = settings.github_api_url.rstrip("/")

    async def create_comment(self, repository: str, number: int, body: str) -> str:
        response = await self._request(
            "POST",
            f"/repos/{repository}/issues/{number}/comments",
            json={"body": body},
        )
        return str(response.get("html_url", ""))

    async def update_pull_request_body(self, repository: str, number: int, body: str) -> None:
        await self._request("PATCH", f"/repos/{repository}/pulls/{number}", json={"body": body})

    async def get_commit_message(self, repository: str, sha: str) -> str:
        """Return a commit message so webhook handlers can ignore Relink's own repair commit."""

        commit = await self._request("GET", f"/repos/{repository}/git/commits/{sha}")
        return str(commit.get("message", ""))

    async def list_pull_request_files(self, repository: str, number: int) -> list[dict[str, Any]]:
        """Return the changed files GitHub reports for one pull request."""

        files = await self._request("GET", f"/repos/{repository}/pulls/{number}/files")
        if not isinstance(files, list):
            raise RuntimeError("GitHub returned an invalid pull-request file list.")
        return [item for item in files if isinstance(item, dict)]

    async def commit_files(
        self,
        repository: str,
        branch: str,
        files: list[FileChange],
        message: str,
    ) -> str:
        """Create one atomic, compare-before-write commit on an existing PR branch."""

        if not files:
            raise ValueError("A repair commit requires at least one file change.")
        paths = [PurePosixPath(change.path) for change in files]
        if any(path.is_absolute() or ".." in path.parts for path in paths):
            raise ValueError("Repair file paths must stay inside the repository.")

        read_ref_path = f"/repos/{repository}/git/ref/heads/{branch}"
        update_ref_path = f"/repos/{repository}/git/refs/heads/{branch}"
        ref: dict[str, Any] = {}
        for attempt in range(3):
            try:
                ref = await self._request("GET", read_ref_path)
                break
            except httpx.HTTPStatusError as exc:
                # A just-opened pull request can briefly race GitHub's ref API even
                # though the webhook payload already names the branch. Retry only
                # that eventual-consistency case; every other response fails fast.
                if exc.response.status_code != 404 or attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        parent_sha = str(ref.get("object", {}).get("sha", ""))
        if not parent_sha:
            raise RuntimeError(f"GitHub returned no head commit for branch {branch!r}.")
        parent = await self._request("GET", f"/repos/{repository}/git/commits/{parent_sha}")
        base_tree = str(parent.get("tree", {}).get("sha", ""))
        if not base_tree:
            raise RuntimeError(f"GitHub returned no base tree for branch {branch!r}.")

        tree_entries: list[dict[str, str]] = []
        for change, path in zip(files, paths, strict=True):
            current = await self._request(
                "GET",
                f"/repos/{repository}/contents/{path.as_posix()}",
                params={"ref": branch},
            )
            encoded = str(current.get("content", "")).replace("\n", "")
            current_text = base64.b64decode(encoded).decode() if encoded else ""
            if change.previous_content is not None and current_text != change.previous_content:
                raise RuntimeError(
                    f"Refusing to overwrite {path}: the PR branch changed after Relink generated its repair."
                )
            blob = await self._request(
                "POST",
                f"/repos/{repository}/git/blobs",
                json={"content": change.content, "encoding": "utf-8"},
            )
            tree_entries.append(
                {"path": path.as_posix(), "mode": "100644", "type": "blob", "sha": str(blob["sha"])}
            )

        tree = await self._request(
            "POST",
            f"/repos/{repository}/git/trees",
            json={"base_tree": base_tree, "tree": tree_entries},
        )
        commit = await self._request(
            "POST",
            f"/repos/{repository}/git/commits",
            json={"message": message, "tree": str(tree["sha"]), "parents": [parent_sha]},
        )
        commit_sha = str(commit.get("sha", ""))
        if not commit_sha:
            raise RuntimeError("GitHub returned no SHA for the repair commit.")
        # GitHub uses singular `ref` for reads and plural `refs` for updates.
        await self._request("PATCH", update_ref_path, json={"sha": commit_sha, "force": False})
        return str(commit.get("html_url") or f"https://github.com/{repository}/commit/{commit_sha}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        token = await self._installation_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "relink-github-app",
        }
        async with httpx.AsyncClient(base_url=self.api_url, timeout=20.0, headers=headers) as client:
            response = await client.request(method, path, json=json, params=params)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def _installation_token(self) -> str:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt()}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "relink-github-app",
        }
        async with httpx.AsyncClient(base_url=self.api_url, timeout=20.0, headers=headers) as client:
            response = await client.post(f"/app/installations/{self.installation_id}/access_tokens")
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            raise RuntimeError("GitHub returned no installation access token.")
        return str(token)

    def _app_jwt(self) -> str:
        now = int(time.time())
        header = _base64url({"alg": "RS256", "typ": "JWT"})
        payload = _base64url({"iat": now - 60, "exp": now + 540, "iss": self.app_id})
        signing_input = f"{header}.{payload}".encode()
        if self.private_key is not None:
            pem = self.private_key.get_secret_value().replace("\\n", "\n").encode()
        elif self.private_key_path is not None:
            pem = self.private_key_path.read_bytes()
        else:  # pragma: no cover - guarded by __init__
            raise RuntimeError("GitHub App private key is unavailable.")
        private_key = serialization.load_pem_private_key(pem, password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _base64url(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def matches_demo_trigger(
    scenario: str,
    event: str,
    item: dict[str, Any],
    files: list[dict[str, Any]] | None = None,
) -> bool:
    """Scope the bundled showcase to the issue proposal or actual seed-header rename."""

    if scenario != "rename_jaffle_user_id":
        return True
    if event == "issues":
        text = f"{item.get('title', '')}\n{item.get('body', '')}".casefold()
        return "raw_orders" in text and "user_id" in text and "customer_user_id" in text
    if event == "pull_request":
        for changed in files or []:
            if str(changed.get("filename", "")) != "seeds/raw_orders.csv":
                continue
            patch = str(changed.get("patch", ""))
            if "-id,user_id,order_date,status" in patch and "+id,customer_user_id,order_date,status" in patch:
                return True
        return False
    return False
