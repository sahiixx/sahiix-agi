"""Ecosystem node discovery and health monitoring for SAHIIX AGI."""
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp


@dataclass
class EcosystemNode:
    name: str
    url: str
    health_endpoint: str
    description: str
    priority: int = 0
    last_seen: float = 0.0
    healthy: bool = False
    latency_ms: float = 0.0
    metadata: dict = field(default_factory=dict)


class EcosystemDiscovery:
    """Discovers and monitors sibling AGI systems in the SAHIIXX ecosystem."""

    def __init__(self):
        self.nodes: Dict[str, EcosystemNode] = {}
        self._init_defaults()

    def _init_defaults(self):
        self.nodes["sahiix-agi"] = EcosystemNode(
            name="sahiix-agi",
            url="http://localhost:7777",
            health_endpoint="/api/status",
            description="Meta-orchestrator (this system)",
            priority=0,
            healthy=True,
            last_seen=time.time(),
        )
        self.nodes["codex-self"] = EcosystemNode(
            name="codex-self",
            url="http://localhost:9001",
            health_endpoint="/health",
            description="Codex self-review agent (GitHub Copilot bridge)",
            priority=1,
        )
        self.nodes["n8n"] = EcosystemNode(
            name="n8n",
            url="http://localhost:5678",
            health_endpoint="/healthz",
            description="n8n workflow automation",
            priority=2,
        )
        self.nodes["open-webui"] = EcosystemNode(
            name="open-webui",
            url="http://localhost:8080",
            health_endpoint="/",
            description="Open WebUI LLM chat interface",
            priority=3,
        )
        self.nodes["ollama"] = EcosystemNode(
            name="ollama",
            url="http://localhost:11434",
            health_endpoint="/api/tags",
            description="Local LLM inference server",
            priority=4,
        )
        self.nodes["redis"] = EcosystemNode(
            name="redis",
            url="redis://localhost:6379",
            health_endpoint="",
            description="Redis cache and message broker",
            priority=5,
        )
        self.nodes["qdrant"] = EcosystemNode(
            name="qdrant",
            url="http://localhost:6333",
            health_endpoint="/collections",
            description="Qdrant vector database",
            priority=6,
        )
        # Expected but currently offline
        self.nodes["sahiixx-os"] = EcosystemNode(
            name="sahiixx-os",
            url="http://localhost:1300",
            health_endpoint="/health",
            description="Real Estate CRM + Pipeline + Outreach (SAHIIXX-OS)",
            priority=10,
        )
        self.nodes["agency-agents"] = EcosystemNode(
            name="agency-agents",
            url="http://localhost:8766",
            health_endpoint="/health",
            description="Multi-agent swarm (152 agents, A2A protocol)",
            priority=11,
        )
        self.nodes["sovereign-swarm"] = EcosystemNode(
            name="sovereign-swarm",
            url="http://localhost:8767",
            health_endpoint="/health",
            description="Sovereign swarm orchestrator",
            priority=12,
        )
        self.nodes["moltworker"] = EcosystemNode(
            name="moltworker",
            url="http://localhost:8787",
            health_endpoint="/health",
            description="Moltworker task executor",
            priority=13,
        )

    async def probe(self, node: EcosystemNode) -> bool:
        try:
            start = time.monotonic()
            # Handle non-HTTP protocols (redis, etc.)
            if node.url.startswith("redis://"):
                import socket
                host_port = node.url.replace("redis://", "").split("/")[0]
                host, port = (host_port.split(":")[0], int(host_port.split(":")[1])) if ":" in host_port else (host_port, 6379)
                with socket.create_connection((host, port), timeout=2):
                    pass
                node.latency_ms = round((time.monotonic() - start) * 1000, 2)
                node.healthy = True
                node.last_seen = time.time()
                return True
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(node.url + node.health_endpoint) as resp:
                    await resp.text()
                    node.latency_ms = round((time.monotonic() - start) * 1000, 2)
                    node.healthy = resp.status < 500
                    node.last_seen = time.time()
                    return node.healthy
        except Exception:
            node.healthy = False
            node.latency_ms = -1.0
            node.last_seen = time.time()
            return False

    async def probe_all(self) -> Dict[str, bool]:
        results = await asyncio.gather(
            *[self.probe(n) for n in self.nodes.values() if n.name != "sahiix-agi"],
            return_exceptions=True
        )
        names = [n.name for n in self.nodes.values() if n.name != "sahiix-agi"]
        out = {name: (r if not isinstance(r, Exception) else False) for name, r in zip(names, results)}
        out["sahiix-agi"] = True
        return out

    def get_available(self) -> List[EcosystemNode]:
        return sorted([n for n in self.nodes.values() if n.healthy], key=lambda x: x.priority)

    def get_status(self) -> Dict[str, dict]:
        return {
            name: {
                "healthy": n.healthy,
                "latency_ms": n.latency_ms,
                "last_seen": n.last_seen,
                "description": n.description,
                "url": n.url,
            }
            for name, n in self.nodes.items()
        }


class EcosystemBridge:
    """HTTP bridge to dispatch tasks to sibling systems."""

    def __init__(self, discovery: EcosystemDiscovery):
        self.discovery = discovery

    async def dispatch_to(self, node_name: str, endpoint: str, payload: dict, method: str = "POST") -> dict:
        node = self.discovery.nodes.get(node_name)
        if not node:
            return {"error": f"Node '{node_name}' not found"}
        url = node.url + endpoint
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if method.upper() == "POST":
                    async with session.post(url, json=payload) as resp:
                        return {
                            "status": resp.status,
                            "data": await resp.json() if resp.content_type == "application/json" else await resp.text()
                        }
                else:
                    async with session.get(url, params=payload) as resp:
                        return {
                            "status": resp.status,
                            "data": await resp.json() if resp.content_type == "application/json" else await resp.text()
                        }
        except Exception as e:
            return {"error": str(e)}

    async def dispatch_chat(self, node_name: str, message: str, context: dict = None) -> str:
        """Dispatch a chat message to a sibling system and return the text response."""
        if node_name == "sahiixx-os":
            result = await self.dispatch_to(node_name, "/trigger/brief", {})
        elif node_name == "agency-agents":
            result = await self.dispatch_to(node_name, "/a2a/chat", {
                "input": message,
                "skill": "chat",
                "max_tokens": 1024,
            })
        elif node_name == "sovereign-swarm":
            result = await self.dispatch_to(node_name, "/api/mission", {
                "mission": message,
                "preset": context.get("preset", "full") if context else "full"
            })
        elif node_name == "moltworker":
            result = await self.dispatch_to(node_name, "/execute", {
                "task": message,
                "context": context or {}
            })
        else:
            return f"[Error] Unknown node: {node_name}"

        if "error" in result:
            return f"[Bridge Error] {result['error']}"

        data = result.get("data", {})
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("response", "result", "output", "verdict", "final", "content", "message"):
                if key in data and isinstance(data[key], str):
                    return data[key]
            return json.dumps(data, indent=2)
        return str(data)

    # ── CRM Bridge Methods ─────────────────────────────────────────────

    async def crm_get_hot_leads(self, limit: int = 20) -> list:
        """Fetch hot leads from SAHIIXX-OS CRM."""
        result = await self.dispatch_to("sahiixx-os", "/crm/hot", {}, method="GET")
        if "error" in result:
            return []
        data = result.get("data", {})
        leads = data.get("hot_leads", []) if isinstance(data, dict) else []
        return leads[:limit]

    async def crm_get_pipeline(self) -> dict:
        """Fetch pipeline summary from SAHIIXX-OS CRM."""
        result = await self.dispatch_to("sahiixx-os", "/pipeline", {}, method="GET")
        if "error" in result:
            return {}
        data = result.get("data", {})
        return data if isinstance(data, dict) else {}

    async def crm_trigger_outreach(self, lead_ids: list = None, dry_run: bool = True) -> dict:
        """Trigger outreach dispatch via SAHIIXX-OS."""
        result = await self.dispatch_to("sahiixx-os", "/run/pipeline", {})
        return result

    async def crm_get_summary(self) -> dict:
        """Fetch CRM summary (lead counts, signals, etc.)."""
        result = await self.dispatch_to("sahiixx-os", "/crm/summary", {}, method="GET")
        if "error" in result:
            return {}
        data = result.get("data", {})
        return data if isinstance(data, dict) else {}
