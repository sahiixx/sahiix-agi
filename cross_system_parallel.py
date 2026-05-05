#!/usr/bin/env python3
"""
cross_system_parallel.py — Run the SAME task on ALL 3 ecosystem systems simultaneously.

SAHIIX AGI (6 agents) + agency-agents (A2A) + goose-aios — all working in parallel.
"""
import asyncio
import aiohttp
import time
import json

SYSTEMS = {
    "sahiix-agi": {
        "url": "http://localhost:7778/api/chat",
        "payload": lambda task: {"message": task, "agent": "director"},
        "headers": {"Content-Type": "application/json"},
    },
    "agency-agents": {
        "url": "http://localhost:8766/a2a/chat",
        "payload": lambda task: {"input": task, "skill": "chat", "max_tokens": 1024},
        "headers": {"Content-Type": "application/json"},
    },
    "goose-aios": {
        "url": "http://localhost:8765/chat",
        "payload": lambda task: {"message": task, "model": "default"},
        "headers": {"Content-Type": "application/json"},
    },
}

TASK = "What's 2+2? Explain briefly."

async def dispatch(name: str, config: dict, task: str, timeout: int = 30):
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config["url"],
                json=config["payload"](task),
                headers=config["headers"],
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                latency = round((time.monotonic() - start) * 1000, 2)
                return {
                    "system": name,
                    "status": "ok",
                    "latency_ms": latency,
                    "response": data,
                }
    except Exception as e:
        return {
            "system": name,
            "status": "error",
            "latency_ms": round((time.monotonic() - start) * 1000, 2),
            "error": str(e),
        }

async def main():
    print("=" * 80)
    print("  CROSS-SYSTEM PARALLEL EXECUTION")
    print("  Task:", TASK[:70] + "...")
    print("  Systems:", list(SYSTEMS.keys()))
    print("=" * 80)
    print()

    # Dispatch to all 3 systems concurrently
    start = time.monotonic()
    results = await asyncio.gather(
        *[dispatch(name, cfg, TASK) for name, cfg in SYSTEMS.items()],
        return_exceptions=True,
    )
    total = round((time.monotonic() - start) * 1000, 2)

    print(f"All 3 systems responded in {total}ms")
    print()

    for r in results:
        sys_name = r.get("system", "unknown")
        latency = r.get("latency_ms", 0)
        status = r.get("status", "unknown")

        print(f"--- {sys_name.upper()} ({latency}ms | {status}) ---")
        if status == "ok":
            data = r.get("response", {})
            # Extract text from various response formats
            text = (
                data.get("response", "")
                or data.get("output", "")
                or str(data)[:500]
            )
            print(text[:400] + ("..." if len(text) > 400 else ""))
        else:
            print(f"ERROR: {r.get('error', 'Unknown error')}")
        print()

    print("=" * 80)
    print(f"Parallel execution complete. Total wall-clock time: {total}ms")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
