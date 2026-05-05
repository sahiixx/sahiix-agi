#!/usr/bin/env python3
"""SAHIIX AGI Swarm Coordinator — Multi-agent dispatch and consensus engine.

This module coordinates Claude, OpenCode, Hermes, OpenClaw, and Codex
to work as a unified swarm on complex tasks.
"""
import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
import aiohttp


@dataclass
class SwarmTask:
    task_id: str
    description: str
    mode: str  # "single", "parallel", "pipeline", "consensus"
    agents: List[str]
    payload: Dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed
    results: Dict[str, Any] = None
    started_at: float = 0.0
    completed_at: float = 0.0


class SwarmCoordinator:
    """Orchestrates the SAHIIX AGI agent swarm."""

    AGENT_ENDPOINTS = {
        "claude": "http://localhost:7777/api/chat",          # Via SAHIIX-AGI
        "opencode": "http://localhost:7777/api/chat",        # Via SAHIIX-AGI (this session)
        "hermes": "http://localhost:8766/chat",              # Hermes bridge
        "openclaw": "http://localhost:8787/api/chat",         # OpenClaw explorer
        "codex": "http://localhost:9001/api/chat",           # Codex CLI
    }

    AGENT_CAPABILITIES = {
        "claude": ["architecture", "planning", "review", "reasoning"],
        "opencode": ["execution", "tool_use", "parallelization", "speed"],
        "hermes": ["messaging", "cross_platform", "notifications", "comms"],
        "openclaw": ["exploration", "research", "crawling", "generation"],
        "codex": ["code_review", "pr_creation", "deployment", "validation"],
    }

    def __init__(self, base_url: str = "http://localhost:7777"):
        self.base_url = base_url
        self.active_tasks: Dict[str, SwarmTask] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def route_task(self, description: str, mode: str = "parallel", 
                         agents: List[str] = None, payload: Dict[str, Any] = None) -> SwarmTask:
        """Route a task to the swarm with appropriate mode."""
        task_id = f"swarm_{int(time.time() * 1000)}"
        task = SwarmTask(
            task_id=task_id,
            description=description,
            mode=mode,
            agents=agents or ["claude", "opencode"],
            payload=payload or {},
            started_at=time.time(),
            status="running",
            results={}
        )
        self.active_tasks[task_id] = task

        if mode == "single":
            # One agent handles everything
            await self._dispatch_single(task)
        elif mode == "parallel":
            # All agents get same task, results merged
            await self._dispatch_parallel(task)
        elif mode == "pipeline":
            # Sequential: Claude plans -> OpenCode executes -> Codex reviews
            await self._dispatch_pipeline(task)
        elif mode == "consensus":
            # All agents analyze, results are voted/merged
            await self._dispatch_consensus(task)
        else:
            task.status = "failed"
            task.results["error"] = f"Unknown mode: {mode}"

        task.completed_at = time.time()
        if task.status == "running":
            task.status = "completed"
        return task

    async def _dispatch_single(self, task: SwarmTask):
        """Single agent mode — assign to best-fit agent."""
        agent = task.agents[0]
        result = await self._call_agent(agent, task.description, task.payload)
        task.results[agent] = result

    async def _dispatch_parallel(self, task: SwarmTask):
        """Parallel mode — all agents work simultaneously."""
        tasks = [self._call_agent(a, task.description, task.payload) for a in task.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for agent, result in zip(task.agents, results):
            if isinstance(result, Exception):
                task.results[agent] = {"error": str(result), "status": "failed"}
            else:
                task.results[agent] = result

    async def _dispatch_pipeline(self, task: SwarmTask):
        """Pipeline mode — Claude plans, OpenCode executes, Codex validates."""
        # Phase 1: Plan (Claude)
        plan = await self._call_agent("claude", f"Create a plan for: {task.description}", task.payload)
        task.results["plan"] = plan

        # Phase 2: Execute (OpenCode)
        exec_payload = {**task.payload, "plan": plan.get("response", "")}
        execution = await self._call_agent("opencode", f"Execute this plan: {task.description}", exec_payload)
        task.results["execution"] = execution

        # Phase 3: Review (Codex)
        review_payload = {
            "plan": plan,
            "execution": execution,
            "description": task.description
        }
        review = await self._call_agent("codex", "Review this implementation", review_payload)
        task.results["review"] = review

    async def _dispatch_consensus(self, task: SwarmTask):
        """Consensus mode — all agents analyze, vote on best answer."""
        # Get analyses from all agents
        tasks = [self._call_agent(a, f"Analyze and provide your best recommendation for: {task.description}", task.payload)
                 for a in task.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        analyses = {}
        for agent, result in zip(task.agents, results):
            if isinstance(result, Exception):
                analyses[agent] = str(result)
            else:
                analyses[agent] = result.get("response", "")

        # Vote — simple majority or Claude decides
        # For now, return all analyses and mark "consensus" as Claude's synthesis
        task.results["individual_analyses"] = analyses
        
        # Claude synthesizes consensus
        synthesis_payload = {
            "analyses": analyses,
            "task": task.description
        }
        consensus = await self._call_agent("claude", "Synthesize a consensus from these agent analyses", synthesis_payload)
        task.results["consensus"] = consensus

    async def _call_agent(self, agent: str, message: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """Call an agent endpoint with the given message."""
        endpoint = self.AGENT_ENDPOINTS.get(agent, self.AGENT_ENDPOINTS["opancode"])
        payload = payload or {}
        
        try:
            session = await self._get_session()
            body = {
                "message": message,
                "agent": agent,
                "context": payload,
                "swarm": True,
                "timestamp": time.time()
            }
            async with session.post(endpoint, json=body, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": f"HTTP {resp.status}", "status": "failed"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    async def broadcast_event(self, event_type: str, payload: Dict[str, Any]):
        """Broadcast an event to all agents via the event bus."""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/api/events/publish",
                json={"event_type": event_type, "payload": payload, "source": "swarm_coordinator"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_task_status(self, task_id: str) -> Optional[SwarmTask]:
        return self.active_tasks.get(task_id)

    def get_all_tasks(self) -> List[SwarmTask]:
        return list(self.active_tasks.values())

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def get_agent_capabilities(self, agent: str) -> List[str]:
        return self.AGENT_CAPABILITIES.get(agent, [])

    def find_best_agent(self, task_description: str) -> str:
        """Find best-fit agent for a task based on capability keywords."""
        desc_lower = task_description.lower()
        scores = {}
        for agent, caps in self.AGENT_CAPABILITIES.items():
            score = sum(2 for cap in caps if cap in desc_lower)
            scores[agent] = score
        if not scores:
            return "opencode"
        return max(scores, key=scores.get)


async def demo():
    """Demo the swarm coordinator."""
    coordinator = SwarmCoordinator()
    
    print("SAHIIX AGI Swarm Coordinator Demo")
    print("=" * 50)
    
    # Demo 1: Parallel execution
    print("\n[Mode: Parallel] Deploy a web service")
    task = await coordinator.route_task(
        "Deploy a FastAPI web service with health checks and logging",
        mode="parallel",
        agents=["claude", "opencode", "codex"]
    )
    for agent, result in task.results.items():
        print(f"  {agent}: {str(result)[:100]}...")
    
    # Demo 2: Pipeline execution
    print("\n[Mode: Pipeline] Build authentication system")
    task = await coordinator.route_task(
        "Build a JWT authentication system with refresh tokens",
        mode="pipeline",
        agents=["claude", "opencode", "codex"]
    )
    print(f"  Plan: {task.results.get('plan', {})}")
    print(f"  Execution: {task.results.get('execution', {})}")
    print(f"  Review: {task.results.get('review', {})}")
    
    print(f"\nCompleted {len(coordinator.get_all_tasks())} swarm tasks.")
    await coordinator.close()


if __name__ == "__main__":
    asyncio.run(demo())
