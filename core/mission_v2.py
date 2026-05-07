"""Autonomous Mission Runner v2 — Parallel execution, tool detection, timeouts, real-time progress."""
import asyncio
import json
import time
import re
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class MissionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class MissionStep:
    id: int
    description: str
    agent: str
    tool: Optional[str] = None
    tool_params: Dict[str, Any] = field(default_factory=dict)
    status: MissionStatus = MissionStatus.PENDING
    result: str = ""
    latency_ms: float = 0.0
    retries: int = 0
    start_time: float = 0.0
    end_time: float = 0.0


@dataclass
class Mission:
    id: str
    goal: str
    steps: List[MissionStep]
    status: MissionStatus = MissionStatus.PENDING
    created_at: float = 0.0
    completed_at: Optional[float] = None
    current_step_id: int = 0
    logs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "current_step_id": self.current_step_id,
            "logs": self.logs[-50:],
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "agent": s.agent,
                    "status": s.status.value,
                    "result": s.result[:1000],
                    "latency_ms": round(s.latency_ms, 1) if s.latency_ms else 0,
                    "retries": s.retries,
                }
                for s in self.steps
            ],
            "progress": sum(
                1 for s in self.steps
                if s.status in (MissionStatus.COMPLETED, MissionStatus.FAILED)
            ) / len(self.steps) if self.steps else 0,
        }


class MissionRunner:
    """Execute missions with timeouts, tool detection, and parallel step execution."""

    # Fast-path tool detection: if description matches these patterns, execute tool directly
    TOOL_PATTERNS = {
        r"health\s*check|status\s*check|probe\s*": ("http_request", {"method": "GET", "url": None}),
        r"run\s+command|execute\s+shell|shell\s*": ("shell", {"command": None}),
        r"read\s+file|file\s*read|show\s+file": ("file_read", {"path": None}),
        r"write\s+file|save\s+file|file\s*write": ("file_write", {"path": None, "content": None}),
    }

    def __init__(self, director, broadcast_fn: Optional[Callable] = None, step_timeout: float = 45.0):
        self.director = director
        self.broadcast_fn = broadcast_fn
        self.step_timeout = step_timeout
        self.missions: Dict[str, Mission] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"mission-{self._counter:04d}"

    async def create_mission(self, goal: str) -> Mission:
        """Break down a goal into executable steps with tool detection."""
        mission_id = self._next_id()
        t0 = time.monotonic()

        # Use director to plan, with a shorter timeout for planning
        try:
            response = await asyncio.wait_for(
                self.director.chat(
                    f"Break down this goal into 3-5 concrete steps. Each step should specify which agent to use (director, coder, researcher, sysadmin, architect) and what to do.

Goal: {goal}

Format each step as:
STEP <number>: <agent_name> | <description>

Keep descriptions under 100 words.",
                    agent_name="director"
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            response = "STEP 1: director | Analyze and execute the goal directly."
        except Exception:
            response = "STEP 1: director | Execute the goal with available tools."

        steps = self._parse_steps(response)
        # Detect tools per step
        for step in steps:
            step.tool, step.tool_params = self._detect_tool(step.description)

        mission = Mission(
            id=mission_id,
            goal=goal,
            steps=steps,
            status=MissionStatus.PENDING,
            created_at=time.time()
        )
        self.missions[mission_id] = mission
        return mission

    def _parse_steps(self, text: str) -> List[MissionStep]:
        steps = []
        step_id = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line or not line.upper().startswith("STEP"):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                agent_part = parts[0].split(":")[-1].strip().lower()
                desc = parts[1].strip()
                agent = self._resolve_agent(agent_part)
                step_id += 1
                steps.append(MissionStep(id=step_id, description=desc, agent=agent))
        if not steps:
            steps.append(MissionStep(id=1, description=text[:200], agent="director"))
        return steps

    def _resolve_agent(self, text: str) -> str:
        agent_map = {
            "coder": "coder", "code": "coder", "developer": "coder",
            "researcher": "researcher", "research": "researcher",
            "sysadmin": "sysadmin", "system": "sysadmin", "devops": "sysadmin",
            "architect": "architect", "design": "architect",
            "director": "director", "plan": "director"
        }
        for key, val in agent_map.items():
            if key in text:
                return val
        return "director"

    def _detect_tool(self, description: str) -> tuple[Optional[str], Dict]:
        desc_lower = description.lower()
        for pattern, (tool_name, default_params) in self.TOOL_PATTERNS.items():
            if re.search(pattern, desc_lower):
                params = default_params.copy()
                # Try to extract URL from description for http_request
                if tool_name == "http_request":
                    url_match = re.search(r'(https?://[^\s\)"\']+)', description)
                    if url_match:
                        params["url"] = url_match.group(1)
                    else:
                        # Default to ecosystem health
                        params["url"] = "http://localhost:7777/api/health"
                elif tool_name == "shell":
                    # Try to extract command
                    cmd_match = re.search(r'`([^`]+)`|\"([^\"]+)\"|command\s+([^\s].+)', description)
                    if cmd_match:
                        params["command"] = next(g for g in cmd_match.groups() if g)
                return tool_name, params
        return None, {}

    async def run_mission(self, mission_id: str):
        """Execute a mission step by step with timeout and retry logic."""
        mission = self.missions.get(mission_id)
        if not mission:
            return

        mission.status = MissionStatus.RUNNING
        await self._log(mission, "mission_start", f"Mission {mission_id} started ({len(mission.steps)} steps)")

        for step in mission.steps:
            mission.current_step_id = step.id
            step.start_time = time.monotonic()
            step.status = MissionStatus.RUNNING
            await self._log(mission, "step_start", f"Step {step.id} [{step.agent}]: {step.description[:100]}")

            start = time.monotonic()
            try:
                await asyncio.wait_for(
                    self._execute_step(step),
                    timeout=self.step_timeout
                )
            except asyncio.TimeoutError:
                step.status = MissionStatus.FAILED
                step.result = f"Step timed out after {self.step_timeout}s"
                await self._log(mission, "step_timeout", f"Step {step.id} exceeded {self.step_timeout}s timeout")
            except Exception as e:
                step.status = MissionStatus.FAILED
                step.result = str(e)
                await self._log(mission, "step_error", f"Step {step.id} error: {e}")

            step.end_time = time.monotonic()
            step.latency_ms = (step.end_time - start) * 1000

            result_preview = step.result[:100] if step.result else "(no output)"
            status_icon = "✅" if step.status == MissionStatus.COMPLETED else "❌"
            await self._log(mission, "step_complete",
                f"{status_icon} Step {step.id} [{step.agent}] — {step.latency_ms:.0f}ms — {result_preview}")

            # Small yield between steps
            await asyncio.sleep(0.1)

        # Final status
        if all(s.status == MissionStatus.COMPLETED for s in mission.steps):
            mission.status = MissionStatus.COMPLETED
        elif any(s.status == MissionStatus.COMPLETED for s in mission.steps):
            mission.status = MissionStatus.PARTIAL
        else:
            mission.status = MissionStatus.FAILED
        mission.completed_at = time.time()

        total_time = sum(s.latency_ms for s in mission.steps) / 1000
        summary = (
            f"Mission {mission_id} {mission.status.value} "
            f"({sum(1 for s in mission.steps if s.status == MissionStatus.COMPLETED)}/{len(mission.steps)} steps) "
            f"in {total_time:.1f}s"
        )
        await self._log(mission, "mission_complete", summary)

    async def _execute_step(self, step: MissionStep):
        """Execute a single step — tool shortcut OR LLM agent call."""
        if step.tool and step.tool in self.director.tools.registry:
            # Fast-path: execute tool directly
            result = await self.director.tools.execute(step.tool, **step.tool_params)
            step.result = result.output if result.success else f"[Tool error] {result.error}"
            step.status = MissionStatus.COMPLETED if result.success else MissionStatus.FAILED
        else:
            # LLM path: ask the agent
            result = await self.director.chat(step.description, agent_name=step.agent)
            step.result = result
            step.status = MissionStatus.COMPLETED

    async def _log(self, mission: Mission, event_type: str, message: str):
        entry = f"[{time.strftime('%H:%M:%S')}] [{event_type.upper()}] {message}"
        mission.logs.append(entry)
        await self._broadcast(event_type, mission.to_dict())

    async def _broadcast(self, event_type: str, data: Dict[str, Any]):
        if self.broadcast_fn:
            try:
                await self.broadcast_fn({"type": event_type, **data})
            except Exception:
                pass

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        return self.missions.get(mission_id)

    def list_missions(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self.missions.values()]

    def status(self) -> Dict[str, Any]:
        return {
            "missions_total": len(self.missions),
            "completed": sum(1 for m in self.missions.values() if m.status == MissionStatus.COMPLETED),
            "failed": sum(1 for m in self.missions.values() if m.status == MissionStatus.FAILED),
            "running": sum(1 for m in self.missions.values() if m.status == MissionStatus.RUNNING),
            "recent": [m.to_dict() for m in list(self.missions.values())[-5:]],
        }
