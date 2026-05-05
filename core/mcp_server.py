"""MCP (Model Context Protocol) server using stdio transport for SAHIIX AGI."""
import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from tools.registry import ToolRegistry, ToolResult


class MCPServer:
    """MCP server over stdio exposing SAHIIX AGI tools."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self.registry = tool_registry
        self._running = False
        self._methods = {
            "initialize": self._on_initialize,
            "tools/list": self._on_tools_list,
            "tools/call": self._on_tool_call,
            "ping": self._on_ping,
        }

    # ── JSON-RPC helpers ──────────────────────────────────────────────────────

    def _send(self, message: dict):
        raw = json.dumps(message, ensure_ascii=False)
        sys.stdout.write(raw + "\n")
        sys.stdout.flush()

    def _log(self, msg: str):
        # Write logs to stderr so they don't corrupt JSON-RPC over stdout
        sys.stderr.write(f"[MCP] {msg}\n")
        sys.stderr.flush()

    def _make_response(self, id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": id, "result": result}

    def _make_error(self, id: Any, code: int, message: str, data: dict = None) -> dict:
        err: dict = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
        if data:
            err["error"]["data"] = data
        return err

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sahiix-agi-mcp", "version": "2.0.0-rt"},
        }

    def _on_tools_list(self, params: dict) -> dict:
        if not self.registry:
            return {"tools": []}
        tools = []
        for t in self.registry.list_tools():
            tools.append({
                "name": t["name"],
                "description": t["description"],
                "inputSchema": {"type": "object", "properties": {}},
            })
        # Ensure core MCP tools are listed even if registry is empty
        core_tools = ["file_read", "file_write", "shell", "web_search", "browser", "system_info"]
        existing = {t["name"] for t in tools}
        for name in core_tools:
            if name not in existing:
                tools.append({
                    "name": name,
                    "description": f"Core SAHIIX AGI tool: {name}",
                    "inputSchema": {"type": "object", "properties": {}},
                })
        return {"tools": tools}

    async def _on_tool_call(self, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not self.registry:
            return {"content": [{"type": "text", "text": "Tool registry not available"}], "isError": True}
        result = await self.registry.execute(name, **arguments)
        return {
            "content": [{"type": "text", "text": result.output or result.error or ""}],
            "isError": not result.success,
        }

    def _on_ping(self, params: dict) -> dict:
        return {}

    # ── Main loop ────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        self._log("MCP server started on stdio")
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                try:
                    req = json.loads(text)
                except json.JSONDecodeError:
                    self._send(self._make_error(None, -32700, "Parse error"))
                    continue

                req_id = req.get("id")
                method = req.get("method", "")
                params = req.get("params", {})

                handler = self._methods.get(method)
                if not handler:
                    self._send(self._make_error(req_id, -32601, f"Method not found: {method}"))
                    continue

                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(params)
                    else:
                        result = handler(params)
                    self._send(self._make_response(req_id, result))
                except Exception as e:
                    self._log(f"Handler error for {method}: {e}")
                    self._send(self._make_error(req_id, -32603, str(e)))
            except Exception as e:
                self._log(f"Loop error: {e}")
                break

    def stop(self):
        self._running = False

    # ── Convenience: start in background thread/task ──────────────────────────

    def start_background(self) -> Optional[asyncio.Task]:
        if not sys.stdin.isatty():
            self._log("stdin is not a tty; skipping stdio MCP server start")
            return None
        return asyncio.create_task(self.run())


# ── Standalone entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    # When run directly, create a minimal standalone MCP server
    # that can proxy to SAHIIX AGI if the registry module is importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from tools.registry import ToolRegistry
        registry = ToolRegistry({})
    except Exception:
        registry = None
    server = MCPServer(registry)
    asyncio.run(server.run())
