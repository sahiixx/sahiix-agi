#!/usr/bin/env python3
"""SAHIIX AGI v2.5.0-omega — Integration Test Suite

Tests all major API endpoints end-to-end.
Run: cd /home/sahiix/sahiix-agi && python test_integration.py
"""
import asyncio
import sys
import aiohttp
import json

BASE_URL = "http://localhost:7777"
TIMEOUT = aiohttp.ClientTimeout(total=30)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

async def test_endpoint(name: str, method: str, path: str, payload: dict = None, expect_status: int = 200, expect_key: str = None) -> dict:
    """Test a single endpoint and return result."""
    url = f"{BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            if method == "GET":
                async with session.get(url) as resp:
                    data = await resp.json() if resp.content_type == "application/json" else {"raw": await resp.text()}
                    ok = resp.status == expect_status
                    if ok and expect_key and expect_key not in str(data):
                        ok = False
                    return {"name": name, "ok": ok, "status": resp.status, "data": data, "error": None}
            elif method == "POST":
                async with session.post(url, json=payload or {}) as resp:
                    data = await resp.json() if resp.content_type == "application/json" else {"raw": await resp.text()}
                    ok = resp.status == expect_status
                    if ok and expect_key and expect_key not in str(data):
                        ok = False
                    return {"name": name, "ok": ok, "status": resp.status, "data": data, "error": None}
            else:
                return {"name": name, "ok": False, "status": 0, "data": None, "error": f"Unknown method: {method}"}
    except Exception as e:
        return {"name": name, "ok": False, "status": 0, "data": None, "error": str(e)}


async def run_tests():
    print("=" * 70)
    print("SAHIIX AGI v2.5.0-omega — Integration Test Suite")
    print("=" * 70)

    results = []

    # ── Health & Status ──────────────────────────────────────────────────
    print("\n[1/8] Health & Status Endpoints")
    for name, path in [("Health", "/api/health"), ("Status", "/api/status"), ("Config", "/api/config")]:
        r = await test_endpoint(name, "GET", path)
        results.append(r)
        icon = PASS if r["ok"] else FAIL
        print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Agents ───────────────────────────────────────────────────────────
    print("\n[2/8] Agent Endpoints")
    for name, path in [("List Agents", "/api/agents"), ("List Tools", "/api/tools")]:
        r = await test_endpoint(name, "GET", path, expect_key="agents" if "Agents" in name else "tools")
        results.append(r)
        icon = PASS if r["ok"] else FAIL
        extra = ""
        if r["ok"] and r["data"]:
            if "agents" in r["data"]:
                extra = f"(" + str(len(r["data"]["agents"])) + " agents)"
            if "tools" in r["data"]:
                extra = f"(" + str(len(r["data"]["tools"])) + " tools)"
        print(f"  {icon} {r['name']}: status={r['status']} {extra}")

    # ── Chat ─────────────────────────────────────────────────────────────
    print("\n[3/8] Chat Endpoints")
    tests = [
        ("Chat", "/api/chat", {"message": "What is SAHIIX AGI?", "agent": "director"}),
        ("Chat Memory", "/api/chat/memory", {"message": "What is this system?", "agent": "director"}),
        ("Chat Stream", "/api/chat/stream", {"message": "Hello", "agent": "director"}),
        ("Chat Parallel", "/api/chat/parallel", {"message": "Test", "agents": ["coder", "researcher"]}),
    ]
    for name, path, payload in tests:
        r = await test_endpoint(name, "POST", path, payload, expect_status=200 if "stream" not in name else 200)
        results.append(r)
        icon = PASS if r["ok"] else FAIL
        print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Agent Delegation ─────────────────────────────────────────────────
    print("\n[4/8] Agent Delegation")
    r = await test_endpoint("Delegate", "POST", "/api/agents/delegate", {
        "from": "director", "to": "coder", "task": "What is Python?", "context": "integration test"
    })
    results.append(r)
    icon = PASS if r["ok"] else FAIL
    print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Memory ───────────────────────────────────────────────────────────
    print("\n[5/8] Memory Endpoints")
    tests = [
        ("Get Memory", "/api/memory", None),
        ("Search Memory", "/api/memory/search", {"query": "test", "mode": "keyword", "limit": 5}),
    ]
    for name, path, payload in tests:
        r = await test_endpoint(name, "POST" if payload else "GET", path, payload)
        results.append(r)
        icon = PASS if r["ok"] else WARN if "down" in str(r.get("error","")).lower() else FAIL
        print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Mission ──────────────────────────────────────────────────────────
    print("\n[6/8] Mission Endpoints")
    r = await test_endpoint("Create Mission", "POST", "/api/mission", {"goal": "Run integration test mission"})
    results.append(r)
    mission_id = r["data"].get("mission_id", "") if r["data"] else ""
    icon = PASS if r["ok"] else FAIL
    print(f"  {icon} {r['name']}: status={r['status']}, id={mission_id[:20] if mission_id else 'N/A'}")
    if mission_id:
        r2 = await test_endpoint("Get Mission", "GET", f"/api/mission/{mission_id}", None)
        results.append(r2)
        icon2 = PASS if r2["ok"] else FAIL
        print(f"  {icon2} Get Mission: status={r2['status']}")

    # ── Autonomy ─────────────────────────────────────────────────────────
    print("\n[7/8] Autonomy Endpoints")
    r = await test_endpoint("Autonomy Status", "GET", "/api/autonomy/status", None)
    results.append(r)
    icon = PASS if r["ok"] else FAIL
    print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Ecosystem ────────────────────────────────────────────────────────
    print("\n[8/8] Ecosystem Endpoints")
    for name, path in [("Ecosystem Status", "/api/ecosystem/status")]:
        r = await test_endpoint(name, "GET", path)
        results.append(r)
        icon = PASS if r["ok"] else FAIL
        print(f"  {icon} {r['name']}: status={r['status']}")

    # ── Summary ───────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{len(results)} passed | {failed} failed")
    if failed > 0:
        print("\nFailures:")
        for r in results:
            if not r["ok"]:
                err_msg = r['error'] or f'status={r["status"]}'
                print(f"  {FAIL} {r['name']}: {err_msg}")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
