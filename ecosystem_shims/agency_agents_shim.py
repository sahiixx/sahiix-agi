"""Agency-Agents API Shim — exposes /health and /api/agency/mission for ecosystem bridge."""
import sys
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add agency-agents to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "agency-agents"))

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any

app = FastAPI(title="Agency-Agents Shim", version="1.0.0")

_director = None
_executor = ThreadPoolExecutor(max_workers=2)


def _get_director():
    global _director
    if _director is None:
        from agi_director import AGIDirector
        _director = AGIDirector()
    return _director


class MissionRequest(BaseModel):
    mission: str = ""
    preset: str = "full"
    context: Dict[str, Any] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agency-agents", "agents": 152}


@app.post("/api/agency/mission")
async def create_mission(req: MissionRequest):
    """Accept a mission and return a verdict/result with timeout protection."""
    try:
        director = _get_director()
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, director.direct, req.mission, 5),
            timeout=25.0
        )
        return {
            "verdict": result.get("verdict", "Mission completed"),
            "output": result.get("output", str(result)),
            "status": "completed"
        }
    except asyncio.TimeoutError:
        return {
            "verdict": f"Agency accepted mission: {req.mission}",
            "output": "Mission is being processed by the swarm (timeout after 25s).",
            "status": "running"
        }
    except Exception as e:
        return {
            "verdict": f"Agency received mission: {req.mission}",
            "output": f"Error: {e}",
            "status": "error"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")
