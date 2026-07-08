"""Outbound MCP client — stdlib only, transport-injectable.

Speaks the Model Context Protocol (JSON-RPC 2.0) as a client so the
assistant can consume any MCP server's tools. Two transports, both
implemented with the standard library so MCP support adds ZERO
dependencies (same rule as embedders and the mem0 adapter):

  * ``StdioTransport`` — spawns the server as a subprocess and exchanges
    newline-delimited JSON-RPC over stdin/stdout (the MCP stdio framing).
  * ``HttpTransport``  — POSTs JSON-RPC to a streamable-HTTP endpoint,
    accepting both plain-JSON and SSE-framed responses, echoing the
    ``Mcp-Session-Id`` header once the server assigns one.

``MCPClient`` is the protocol layer on top: initialize handshake,
``list_tools``, ``call_tool``. Tests (and fixtures) inject a fake
transport — the client is deterministic given a transport.

Nothing in this module touches a graph or makes policy decisions. The
governance happens in registry.py, where discovered tools become Tool
Gateway capabilities.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import urllib.request
from typing import Any, Optional, Protocol, runtime_checkable

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "activegraph-packs", "version": "0.2"}


class MCPError(RuntimeError):
    """A JSON-RPC level error returned by the MCP server."""


@runtime_checkable
class MCPTransport(Protocol):
    """Anything that can exchange one JSON-RPC message with an MCP server."""

    def request(self, payload: dict) -> dict: ...
    def notify(self, payload: dict) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------- transports


class StdioTransport:
    """MCP stdio framing: one JSON-RPC message per line over a subprocess."""

    def __init__(self, command: list[str], *, env: Optional[dict] = None,
                 timeout: float = 30.0):
        self.timeout = timeout
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )

    def _send(self, payload: dict) -> None:
        if self._proc.stdin is None:
            raise MCPError("stdio transport: process stdin closed")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def request(self, payload: dict) -> dict:
        self._send(payload)
        want_id = payload.get("id")
        # Read lines until the response with our id arrives (servers may
        # interleave notifications, which we skip).
        while True:
            line = self._proc.stdout.readline() if self._proc.stdout else ""
            if not line:
                raise MCPError("stdio transport: server closed the stream")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == want_id:
                return message

    def notify(self, payload: dict) -> None:
        self._send(payload)

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass


class HttpTransport:
    """MCP streamable HTTP: JSON-RPC over POST, JSON or SSE responses."""

    def __init__(self, url: str, *, headers: Optional[dict[str, str]] = None,
                 timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self._headers = dict(headers or {})
        self._session_id: Optional[str] = None

    def _post(self, payload: dict) -> tuple[Optional[dict], dict]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            session = response.headers.get("Mcp-Session-Id")
            if session:
                self._session_id = session
            content_type = (response.headers.get("Content-Type") or "").lower()
            body = response.read().decode()
        if not body.strip():
            return None, {}
        if "text/event-stream" in content_type:
            return self._first_jsonrpc_from_sse(body, payload.get("id")), {}
        return json.loads(body), {}

    @staticmethod
    def _first_jsonrpc_from_sse(body: str, want_id: Any) -> Optional[dict]:
        for chunk in body.split("\n\n"):
            data_lines = [
                line[len("data:"):].strip()
                for line in chunk.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                message = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if message.get("id") == want_id:
                return message
        return None

    def request(self, payload: dict) -> dict:
        message, _ = self._post(payload)
        if message is None:
            raise MCPError(f"no JSON-RPC response for id={payload.get('id')!r}")
        return message

    def notify(self, payload: dict) -> None:
        self._post(payload)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- client


class MCPClient:
    """Protocol-level MCP client over an injected transport."""

    def __init__(self, transport: MCPTransport):
        self._transport = transport
        self._ids = itertools.count(1)
        self._initialized = False
        self.server_info: dict = {}

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0", "id": next(self._ids), "method": method,
        }
        if params is not None:
            payload["params"] = params
        message = self._transport.request(payload)
        if "error" in message:
            err = message["error"]
            raise MCPError(f"{method}: {err.get('message')} (code {err.get('code')})")
        return message.get("result")

    def initialize(self) -> dict:
        """Run the MCP handshake; idempotent."""
        if self._initialized:
            return self.server_info
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }) or {}
        self.server_info = result.get("serverInfo", {})
        self._transport.notify({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        self._initialized = True
        return self.server_info

    def list_tools(self) -> list[dict]:
        """Return the server's tool definitions (name/description/inputSchema)."""
        self.initialize()
        result = self._rpc("tools/list") or {}
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke one tool; returns the raw MCP result (content blocks)."""
        self.initialize()
        return self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}

    def call_tool_text(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Invoke one tool and flatten its content blocks to text.

        Returns (text, is_error). This is the executor-facing shape: the
        gateway stores strings, and non-text blocks are represented by a
        placeholder rather than dropped silently.
        """
        result = self.call_tool(name, arguments)
        parts = []
        for block in result.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(f"[non-text content: {block.get('type', 'unknown')}]")
        return "\n".join(parts), bool(result.get("isError"))

    def close(self) -> None:
        self._transport.close()


def make_client(transport_kind: str, *, url: str = "", command: Optional[list[str]] = None,
                headers: Optional[dict[str, str]] = None, timeout: float = 30.0) -> MCPClient:
    """Build a client from a transport description (the settings shape)."""
    if transport_kind == "http":
        if not url:
            raise ValueError("http transport requires a url")
        return MCPClient(HttpTransport(url, headers=headers, timeout=timeout))
    if transport_kind == "stdio":
        if not command:
            raise ValueError("stdio transport requires a command")
        return MCPClient(StdioTransport(command, timeout=timeout))
    raise ValueError(f"unknown MCP transport {transport_kind!r} (use 'http' or 'stdio')")
