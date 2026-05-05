"""Friday-OS API Shim — exposes /health and /a2a/invoke for ecosystem bridge."""
import sys
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add friday-os to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "friday-os"))

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any

app = FastAPI(title="Friday-OS Shim", version="1.0.0")

_orchestrator = None
_executor = ThreadPoolExecutor(max_workers=2)


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from friday.core import Orchestrator
        _orchestrator = Orchestrator()
    return _orchestrator


class InvokeRequest(BaseModel):
    agent: str = "friday"
    task: str = ""
    context: Dict[str, Any] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "friday-os"}


@app.post("/a2a/invoke")
async def invoke(req: InvokeRequest):
    """Invoke Friday Orchestrator with timeout protection."""
    try:
        orch = _get_orchestrator()
        loop = asyncio.get_event_loop()
        # Run blocking orchestrator in thread with 20s timeout
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, orch.run, req.task),
            timeout=20.0
        )
        output = result.output if hasattr(result, "output") else str(result)
        return {"response": output}
    except asyncio.TimeoutError:
        return {"response": f"[Friday-OS] Task '{req.task}' is being processed (timeout after 20s)."}
    except Exception as e:
        return {"response": f"[Friday-OS error: {e}]"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
