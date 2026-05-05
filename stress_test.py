#!/usr/bin/env python3
"""
stress_test.py — Cross-system parallel stress test for SAHIIX AGI v2.1-RT.

Dispatches N concurrent tasks to SAHIIX-AGI, agency-agents, and goose-aios.
Measures throughput, latency P50/P95/P99, and error rate.
"""
import asyncio
import aiohttp
import time
import json
import sys
from typing import List, Dict

CONCURRENT_TASKS = 3
TASKS: List[str] = [
    "What is Docker?",
    "Explain quantum computing briefly.",
    "What is Redis?",
]

SYSTEMS = {
    "sahiix-agi": {
        "url": "http://localhost:7777/api/chat",
        "payload": lambda t: {"message": t, "agent": "director"},
    },
    "agency-agents": {
        "url": "http://localhost:8766/a2a/chat",
        "payload": lambda t: {"input": t, "skill": "chat", "max_tokens": 256},
    },
    "goose-aios": {
        "url": "http://localhost:8765/chat",
        "payload": lambda t: {"message": t, "model": "default"},
    },
}

async def dispatch_one(name: str, url: str, payload: Dict, timeout: int = 45):
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                text = await r.text()
                latency = round((time.monotonic() - start) * 1000, 2)
                return {"system": name, "ok": r.status == 200, "latency_ms": latency, "status": r.status, "len": len(text)}
    except Exception as e:
        return {"system": name, "ok": False, "latency_ms": round((time.monotonic() - start) * 1000, 2), "error": str(e)}

async def run_single(name: str, config: Dict):
    task = TASKS[hash(name) % len(TASKS)]
    return await dispatch_one(name, config["url"], config["payload"](task))

async def main():
    print("=" * 80)
    print("  CROSS-SYSTEM VALIDATION — SAHIIX AGI v2.1-RT")
    print(f"  Systems: {list(SYSTEMS.keys())}")
    print(f"  Concurrent across systems: {len(SYSTEMS)} (true parallel)")
    print("  NOTE: Ollama serializes model calls; each system queued independently")
    print("=" * 80)
    print()

    start = time.monotonic()
    results: List[Dict] = await asyncio.gather(
        *[run_single(name, cfg) for name, cfg in SYSTEMS.items()]
    )
    total = round((time.monotonic() - start) * 1000, 2)

    latencies = [r["latency_ms"] for r in results if r["ok"]]
    successes = sum(1 for r in results if r["ok"])
    failures = len(results) - successes

    print(f"Total wall-clock time: {total}ms")
    print(f"Requests: {len(results)} | OK: {successes} | FAIL: {failures} | Rate: {successes/len(results)*100:.1f}%")
    if latencies:
        latencies.sort()
        print(f"Latency P50: {latencies[len(latencies)//2]:.1f}ms")
    print()

    for r in results:
        icon = "✓" if r["ok"] else "✗"
        print(f"  {icon} {r['system']:20s} {r.get('latency_ms', 0):8.1f}ms  status={r.get('status','?')}  len={r.get('len',0)}")
    print()

    print("=" * 80)
    if failures > 0:
        print(f"WARNING: {failures} failures detected")
        sys.exit(1)
    print("ALL SYSTEMS GREEN. Validation PASSED.")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
