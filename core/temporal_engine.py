"""Temporal Workflow Engine for SAHIIX AGI — durable, replayable mission execution."""
import asyncio
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

try:
    from temporalio import activity, workflow
    from temporalio.client import Client
    from temporalio.worker import Worker
    from temporalio.common import RetryPolicy
    TEMPORAL_AVAILABLE = True
except Exception:
    TEMPORAL_AVAILABLE = False

    class _WorkflowStub:
        def defn(self, fn):
            return fn
        @staticmethod
        def run(fn):
            return fn

    class _ActivityStub:
        def defn(self, fn):
            return fn

    workflow = _WorkflowStub()
    activity = _ActivityStub()
    Client = None
    Worker = None
    RetryPolicy = None


class MissionActivities:
    """Activities that interact with SAHIIX AGI subsystems."""

    def __init__(self, director):
        self.director = director

    def _parse_steps(self, text: str) -> List[Dict[str, Any]]:
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
                steps.append({"id": step_id, "description": desc, "agent": agent})
        if not steps:
            steps.append({"id": 1, "description": text[:200], "agent": "director"})
        return steps

    @activity.defn
    async def plan_mission(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        goal = params.get("goal", "")
        prompt = (
            f"Break down this goal into 3-5 concrete steps.\n"
            f"Each step should specify which agent to use (director, coder, researcher, sysadmin, architect) and what to do.\n\n"
            f"Goal: {goal}\n\n"
            f"Format each step as:\nSTEP <number>: <agent_name> | <description>\n\nKeep descriptions under 100 words."
        )
        response = await self.director.chat(prompt, agent_name="director")
        return self._parse_steps(response)

    @activity.defn
    async def run_agent(self, params: Dict[str, Any]) -> Dict[str, Any]:
        step = params.get("step", {})
        goal = params.get("goal", "")
        description = step.get("description", "")
        agent_name = step.get("agent", "director")
        try:
            result = await self.director.chat(description, agent_name=agent_name)
            return {
                "step_id": step.get("id"),
                "agent": agent_name,
                "status": "completed",
                "result": result,
                "goal": goal,
            }
        except Exception as e:
            return {
                "step_id": step.get("id"),
                "agent": agent_name,
                "status": "failed",
                "error": str(e),
                "goal": goal,
            }

    @activity.defn
    async def save_checkpoint(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = params.get("mission_id")
        step_id = params.get("step_id")
        result = params.get("result")
        try:
            await self.director.memory.save_episode(
                "system",
                json.dumps({"type": "checkpoint", "mission_id": mission_id, "step_id": step_id, "result": result}),
                agent="temporal",
            )
            return {"saved": True, "mission_id": mission_id, "step_id": step_id}
        except Exception as e:
            return {"saved": False, "error": str(e)}

    @activity.defn
    async def notify(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = params.get("mission_id")
        status = params.get("status")
        return {"notified": True, "mission_id": mission_id, "status": status}


@workflow.defn
class ParallelStepsWorkflow:
    """Child workflow that executes a batch of mission steps in parallel."""

    @workflow.run
    async def run(self, mission_id: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        tasks = []
        for step in steps:
            tasks.append(
                workflow.execute_activity(
                    MissionActivities.run_agent,
                    {"mission_id": mission_id, "step": step},
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            )
        results = await asyncio.gather(*tasks)
        return {"mission_id": mission_id, "results": results}


@workflow.defn
class AgentMissionWorkflow:
    """Durable, replayable mission execution workflow."""

    @workflow.run
    async def run(self, mission_id: str, goal: str) -> Dict[str, Any]:
        steps = await workflow.execute_activity(
            MissionActivities.plan_mission,
            {"mission_id": mission_id, "goal": goal},
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        results: List[Dict[str, Any]] = []

        for step in steps:
            parallel = step.get("parallel", False)
            batch = step.get("batch")
            if parallel and isinstance(batch, list) and len(batch) > 0:
                child_result = await workflow.execute_child_workflow(
                    ParallelStepsWorkflow.run,
                    args=(mission_id, batch),
                    id=f"{mission_id}-parallel-{step.get('id')}",
                    task_queue=workflow.info().task_queue,
                )
                results.extend(child_result.get("results", []))
            else:
                result = await workflow.execute_activity(
                    MissionActivities.run_agent,
                    {"mission_id": mission_id, "step": step, "goal": goal},
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                results.append(result)
                await workflow.execute_activity(
                    MissionActivities.save_checkpoint,
                    {"mission_id": mission_id, "step_id": step.get("id"), "result": result},
                    start_to_close_timeout=timedelta(seconds=10),
                )

        await workflow.execute_activity(
            MissionActivities.notify,
            {"mission_id": mission_id, "status": "completed", "results": results},
            start_to_close_timeout=timedelta(seconds=10),
        )

        return {"mission_id": mission_id, "status": "completed", "results": results}


class TemporalWorkflowEngine:
    """High-level engine to interact with Temporal from SAHIIX AGI."""

    def __init__(
        self,
        director,
        host: str = "localhost:7233",
        namespace: str = "default",
        task_queue: str = "sahiix-agi-missions",
    ):
        if not TEMPORAL_AVAILABLE:
            raise RuntimeError("temporal-sdk (temporalio) is not installed")
        self.director = director
        self.host = host
        self.namespace = namespace
        self.task_queue = task_queue
        self.client: Optional[Client] = None
        self.worker: Optional[Worker] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._activities = MissionActivities(director)

    async def connect(self):
        if self.client is None:
            self.client = await Client.connect(self.host, namespace=self.namespace)

    async def start_worker(self):
        await self.connect()
        self.worker = Worker(
            self.client,
            task_queue=self.task_queue,
            workflows=[AgentMissionWorkflow, ParallelStepsWorkflow],
            activities=[self._activities],
        )
        self._worker_task = asyncio.create_task(self.worker.run())

    async def start_mission(self, mission_id: str, goal: str) -> str:
        await self.connect()
        handle = await self.client.start_workflow(
            AgentMissionWorkflow.run,
            id=mission_id,
            args=(mission_id, goal),
            task_queue=self.task_queue,
        )
        return handle.id

    async def get_mission_status(self, mission_id: str) -> Dict[str, Any]:
        await self.connect()
        try:
            handle = self.client.get_workflow_handle(mission_id)
            desc = await handle.describe()
            return {
                "workflow_id": mission_id,
                "status": desc.status.name if desc.status else "unknown",
                "run_id": desc.run_id,
            }
        except Exception as e:
            return {"workflow_id": mission_id, "status": "not_found", "error": str(e)}

    async def cancel_mission(self, mission_id: str) -> Dict[str, Any]:
        await self.connect()
        try:
            handle = self.client.get_workflow_handle(mission_id)
            await handle.cancel()
            return {"workflow_id": mission_id, "cancelled": True}
        except Exception as e:
            return {"workflow_id": mission_id, "cancelled": False, "error": str(e)}

    async def close(self):
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.close()
            self.client = None
