"""Autonomous Mission Runner for SAHIIX AGI with retry and checkpoint support."""
import asyncio
import time
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


@dataclass
class Mission:
    id: str
    goal: str
    steps: List[MissionStep]
    status: MissionStatus = MissionStatus.PENDING
    created_at: float = 0.0
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "agent": s.agent,
                    "status": s.status.value,
                    "result": s.result[:200],
                    "latency_ms": s.latency_ms,
                    "retries": s.retries,
                }
                for s in self.steps
            ],
            "progress": sum(
                1 for s in self.steps
                if s.status in (MissionStatus.COMPLETED, MissionStatus.FAILED)
            ) / len(self.steps) if self.steps else 0
        }


class MissionRunner:
    def __init__(self, director, broadcast_fn: Optional[Callable] = None):
        self.director = director
        self.broadcast_fn = broadcast_fn
        self.missions: Dict[str, Mission] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"mission-{self._counter:04d}"

    async def create_mission(self, goal: str) -> Mission:
        """Break down a goal into executable steps."""
        mission_id = self._next_id()

        plan_prompt = f"""Break down this goal into 3-5 concrete steps.
Each step should specify which agent to use (director, coder, researcher, sysadmin, architect) and what to do.

Goal: {goal}

Format each step as:
STEP <number>: <agent_name> | <description>

Keep descriptions under 100 words."""

        response = await self.director.chat(plan_prompt, agent_name="director")
        steps = self._parse_steps(response)

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

                agent_map = {
                    "coder": "coder", "code": "coder", "developer": "coder",
                    "researcher": "researcher", "research": "researcher",
                    "sysadmin": "sysadmin", "system": "sysadmin", "devops": "sysadmin",
                    "architect": "architect", "design": "architect",
                    "director": "director", "plan": "director"
                }
                agent = "director"
                for key, val in agent_map.items():
                    if key in agent_part:
                        agent = val
                        break

                step_id += 1
                steps.append(MissionStep(id=step_id, description=desc, agent=agent))

        if not steps:
            steps.append(MissionStep(id=1, description=text[:200], agent="director"))

        return steps

    async def run_mission(self, mission_id: str):
        """Execute a mission step by step with retry logic."""
        mission = self.missions.get(mission_id)
        if not mission:
            return

        mission.status = MissionStatus.RUNNING
        await self._broadcast("mission_start", mission.to_dict())

        for step in mission.steps:
            step.status = MissionStatus.RUNNING
            await self._broadcast("mission_step_start", {
                "mission_id": mission.id,
                "step": step.id,
                "description": step.description
            })

            start = time.monotonic()
            result = await self._execute_step_with_retry(step)
            step.latency_ms = (time.monotonic() - start) * 1000

            await self._broadcast("mission_step_complete", {
                "mission_id": mission.id,
                "step": step.id,
                "status": step.status.value,
                "latency_ms": step.latency_ms
            })

            await asyncio.sleep(0.5)

        # Determine final status
        if all(s.status == MissionStatus.COMPLETED for s in mission.steps):
            mission.status = MissionStatus.COMPLETED
        elif any(s.status == MissionStatus.COMPLETED for s in mission.steps):
            mission.status = MissionStatus.PARTIAL
        else:
            mission.status = MissionStatus.FAILED
        mission.completed_at = time.time()
        await self._broadcast("mission_complete", mission.to_dict())

    async def _execute_step_with_retry(self, step: MissionStep, max_retries: int = 1):
        """Execute a step with optional retry on failure."""
        for attempt in range(max_retries + 1):
            try:
                result = await self.director.chat(step.description, agent_name=step.agent)
                step.result = result
                step.status = MissionStatus.COMPLETED
                return
            except Exception as e:
                step.result = str(e)
                step.retries = attempt + 1
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                else:
                    step.status = MissionStatus.FAILED

    async def _broadcast(self, event_type: str, data: Dict[str, Any]):
        if self.broadcast_fn:
            try:
                await self.broadcast_fn({"type": event_type, **data})
            except Exception:
                pass

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        return self.missions.get(mission_id)

    def list_missions(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in sorted(self.missions.values(), key=lambda x: x.created_at, reverse=True)]
