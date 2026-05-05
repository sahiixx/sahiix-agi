"""SAHIIX AGI Orchestrator — Real-time parallel execution engine with deadlock-free health checks."""
import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, AsyncIterator
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import orjson
import yaml

from core.llm import LLMManager
from memory.store import MemoryStore
from tools.registry import ToolRegistry
from agents.base import Agent, AgentConfig

try:
    from agents.specialized import AGENT_CLASS_MAP
except Exception:
    AGENT_CLASS_MAP = {}

try:
    from core.ecosystem import EcosystemDiscovery, EcosystemBridge
    from core.unified_router import UnifiedRouter
except Exception:
    EcosystemDiscovery = None
    EcosystemBridge = None
    UnifiedRouter = None


class Director:
    """Meta-director with parallel agent execution, predictive routing, and ecosystem unification."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.llm = LLMManager(self.config.get("llm", {}))
        self.memory = MemoryStore(self.config.get("memory", {}).get("path", "/tmp/agi_memory.db"))
        self.tools = ToolRegistry(self.config.get("tools", {}))
        self.agents: Dict[str, Agent] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._warm_data: Dict[str, Any] = {}
        self._eco_status_cache: Dict[str, Any] = {}
        self._eco_cache_time = 0.0

        # Ecosystem integration
        if EcosystemDiscovery:
            self.ecosystem = EcosystemDiscovery()
            self.bridge = EcosystemBridge(self.ecosystem)
            self.router = UnifiedRouter(self.ecosystem, self.bridge)
        else:
            self.ecosystem = None
            self.bridge = None
            self.router = None

        self._init_agents()

    def _init_agents(self):
        for agent_cfg in self.config.get("agents", []):
            cfg = AgentConfig(**{k: v for k, v in agent_cfg.items() if k in AgentConfig.__dataclass_fields__})
            agent_cls = AGENT_CLASS_MAP.get(cfg.name, Agent)
            self.agents[cfg.name] = agent_cls(cfg, self.llm, self.memory, self.tools)

    async def warmup(self):
        """Pre-warm all subsystems without deadlocking."""
        await asyncio.gather(
            self.llm.warmup(),
            self.memory.init(),
            self.tools.warmup(),
            return_exceptions=True
        )
        # Preload graph data into warm cache
        graph_path = self.config.get("tools", {}).get("repo_knowledge", {}).get("graph_data_path", "")
        if graph_path and Path(graph_path).exists():
            with open(graph_path) as f:
                self._warm_data["graph"] = orjson.loads(f.read())
        # Probe ecosystem siblings ONLY (skip self to avoid deadlock)
        if self.ecosystem:
            await self._probe_ecosystem_siblings()

    async def _probe_ecosystem_siblings(self):
        """Probe ecosystem nodes except self to prevent HTTP deadlock."""
        tasks = []
        names = []
        for name, node in self.ecosystem.nodes.items():
            if name == "sahiix-agi":
                # Mark self healthy without probing
                node.healthy = True
                node.last_seen = time.time()
                node.latency_ms = 0.0
                continue
            tasks.append(self.ecosystem.probe(node))
            names.append(name)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, r in zip(names, results):
                if isinstance(r, Exception):
                    self.ecosystem.nodes[name].healthy = False

    def _detect_specialist(self, text: str) -> Optional[str]:
        text = text.lower()
        keywords = {
            "coder": ["code", "program", "bug", "fix", "error", "python", "javascript", "function", "class", "api", "refactor", "debug"],
            "researcher": ["research", "find", "search", "discover", "analyze", "trend", "github", "repo", "study", "compare"],
            "sysadmin": ["system", "server", "docker", "deploy", "config", "service", "systemd", "optimize", "tune", "log"],
            "architect": ["design", "architecture", "structure", "pattern", "scalable", "microservice", "system design", "diagram"],
            "dataengineer": ["sql", "database", "etl", "pipeline", "schema", "table", "query", "dataframe", "pandas", "dbt", "warehouse", "lake", "postgres", "mysql", "mongo", "redis", "kafka", "spark", "airflow", "dask", "csv", "jsonl", "parquet", "arrow", "duckdb", "sqlite", "migration", "seed", "transform", "load", "extract", "validate", "clean", "normalize", "denormalize", "index", "partition", "shard", "replica", "backup", "restore", "dump", "import", "export"],
        }
        scores = {k: sum(1 for w in v if w in text) for k, v in keywords.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else None

    async def route(self, user_input: str) -> str:
        specialist = self._detect_specialist(user_input)
        if specialist and specialist in self.agents:
            agent = self.agents[specialist]
            context = f"Routed to specialist: {specialist}"
        else:
            agent = self.agents.get("director", list(self.agents.values())[0])
            context = ""
        return await agent.run(user_input, context)

    async def chat(self, user_input: str, agent_name: Optional[str] = None, use_unified: bool = True) -> str:
        if agent_name and agent_name in self.agents:
            return await self.agents[agent_name].run(user_input)
        # Try unified routing to ecosystem siblings first
        if use_unified and self.router:
            route_result = await self.router.route(user_input)
            if route_result.get("dispatched"):
                return f"[{route_result['node'].upper()} | confidence {route_result['confidence']}]{chr(10)}{route_result['result']}"
        return await self.route(user_input)

    async def parallel_chat(self, user_input: str, agents: List[str] = None) -> Dict[str, str]:
        """Execute multiple agents in parallel and return aggregated results."""
        targets = agents or list(self.agents.keys())
        tasks = {name: self.agents[name].run(user_input) for name in targets if name in self.agents}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            name: (result if not isinstance(result, Exception) else f"Error: {result}")
            for name, result in zip(tasks.keys(), results)
        }

    async def stream_chat(self, user_input: str, agent_name: Optional[str] = None) -> AsyncIterator[str]:
        if agent_name and agent_name in self.agents:
            async for chunk in self.agents[agent_name].stream(user_input):
                yield chunk
        else:
            specialist = self._detect_specialist(user_input)
            if specialist and specialist in self.agents:
                async for chunk in self.agents[specialist].stream(user_input):
                    yield chunk
            else:
                # Fallback: route returns full string, yield as single chunk
                result = await self.route(user_input)
                yield result

    async def delegate(self, from_agent: str, to_agent: str, task: str, context: str = "") -> str:
        """Delegate a task from one agent to another with context."""
        if to_agent not in self.agents:
            return f"[Error] Agent '{to_agent}' not found"
        ctx = f"Delegated by {from_agent}. {context}".strip()
        return await self.agents[to_agent].run(task, context=ctx)

    async def get_ecosystem_status(self) -> Dict[str, Any]:
        if not self.ecosystem:
            return {"error": "Ecosystem integration not available"}
        # Refresh cache if stale (>30s)
        if time.time() - self._eco_cache_time > 30:
            await self._probe_ecosystem_siblings()
            self._eco_status_cache = self.ecosystem.get_status()
            self._eco_cache_time = time.time()
        return self._eco_status_cache

    def get_status(self) -> Dict[str, Any]:
        return {
            "system": self.config.get("system", {}),
            "agents": list(self.agents.keys()),
            "tools": [t["name"] for t in self.tools.list_tools()],
            "memory_episodes": 0,
            "warm_data_keys": list(self._warm_data.keys()),
            "ecosystem": self.ecosystem.get_status() if self.ecosystem else {},
        }

    async def get_full_status(self) -> Dict[str, Any]:
        episodes = await self.memory.get_recent(limit=1000)
        eco = await self.get_ecosystem_status()
        return {
            "system": self.config.get("system", {}),
            "agents": list(self.agents.keys()),
            "tools": [t["name"] for t in self.tools.list_tools()],
            "memory_episodes": len(episodes),
            "warm_data_keys": list(self._warm_data.keys()),
            "ecosystem": eco,
        }

    async def close(self):
        self._executor.shutdown(wait=False)
        await self.llm.close()
        await self.memory.close()
