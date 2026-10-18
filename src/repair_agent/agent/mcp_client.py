"""Actual MCP client transport used by the Groq agent loop."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from repair_agent.config import Settings


@dataclass(frozen=True)
class MCPToolSpec:
    """Provider-neutral subset of an MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


class MCPToolClient(Protocol):
    """Small async boundary that keeps the planner testable over real MCP semantics."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def list_tools(self) -> list[MCPToolSpec]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class FalkorDBStdioMCPClient:
    """Launch the FalkorDB MCP server and communicate with it using JSON-RPC/stdin."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._transport_context: Any = None
        self._session_context: ClientSession | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        environment = {
            "FALKORDB_HOST": self.settings.falkordb_host,
            "FALKORDB_PORT": str(self.settings.falkordb_port),
            "FALKORDB_USERNAME": self.settings.falkordb_username,
            "FALKORDB_PASSWORD": self.settings.falkordb_password,
            "FALKORDB_GRAPH": self.settings.falkordb_graph,
            "FALKORDB_SSL": str(self.settings.falkordb_ssl).lower(),
        }
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "repair_agent.falkordb_io.mcp"],
            env=environment,
            cwd=self.settings.repo_root,
        )
        self._transport_context = stdio_client(server)
        read_stream, write_stream = await self._transport_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        self._session = await self._session_context.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(exc_type, exc, traceback)
        if self._transport_context is not None:
            await self._transport_context.__aexit__(exc_type, exc, traceback)
        self._session = None

    async def list_tools(self) -> list[MCPToolSpec]:
        session = self._require_session()
        response = await session.list_tools()
        return [
            MCPToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
            )
            for tool in response.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self._require_session().call_tool(name, arguments)
        if response.isError:
            detail = " ".join(block.text for block in response.content if isinstance(block, TextContent))
            raise RuntimeError(f"MCP tool {name!r} failed: {detail or 'unknown server error'}")
        structured = response.structuredContent
        if structured is not None:
            # FastMCP may wrap non-model return values in a top-level result field.
            if set(structured) == {"result"} and isinstance(structured["result"], dict):
                return structured["result"]
            return structured
        for block in response.content:
            if isinstance(block, TextContent):
                value = json.loads(block.text)
                if isinstance(value, dict):
                    return value
        raise RuntimeError(f"MCP tool {name!r} returned no structured object result.")

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP client must be entered before use.")
        return self._session
