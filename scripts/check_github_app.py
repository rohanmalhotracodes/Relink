"""Print non-secret GitHub App installation diagnostics."""

from __future__ import annotations

import asyncio

import httpx

from repair_agent.config import get_settings
from repair_agent.github_app import GITHUB_API_VERSION, GitHubAppClient


async def main() -> None:
    settings = get_settings()
    client = GitHubAppClient(settings, 0)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {client._app_jwt()}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "relink-github-app-diagnostics",
    }
    async with httpx.AsyncClient(base_url=settings.github_api_url, headers=headers, timeout=20.0) as http:
        app_response = await http.get("/app")
        app_response.raise_for_status()
        installations_response = await http.get("/app/installations")
        installations_response.raise_for_status()

    app = app_response.json()
    print(f"App: {app.get('slug', '(unknown)')}")
    for installation in installations_response.json():
        account = installation.get("account", {}).get("login", "(unknown)")
        print(
            f"Installation {installation.get('id')} on {account}: "
            f"permissions={installation.get('permissions', {})}"
        )


if __name__ == "__main__":
    asyncio.run(main())
