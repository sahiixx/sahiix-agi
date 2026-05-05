"""Prometheus metrics exporter for SAHIIX AGI."""
import asyncio
import json
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import psutil
import aiohttp
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client.registry import REGISTRY

# ── Define metrics ───────────────────────────────────────────────
AGI_REQUESTS_TOTAL = Counter('sahiix_agi_requests_total', 'Total API requests', ['method', 'endpoint', 'status'])
AGI_REQUEST_LATENCY = Histogram('sahiix_agi_request_latency_seconds', 'Request latency', ['endpoint'])
AGI_ACTIVE_WS = Gauge('sahiix_agi_active_websockets', 'Active WebSocket connections')
AGI_AGENTS_TOTAL = Gauge('sahiix_agi_agents_total', 'Number of registered agents')
AGI_TOOLS_TOTAL = Gauge('sahiix_agi_tools_total', 'Number of registered tools')
AGI_MEMORY_EPISODES = Gauge('sahiix_agi_memory_episodes', 'Total memory episodes')
AGI_AUTONOMY_ENABLED = Gauge('sahiix_agi_autonomy_enabled', 'Autonomy engine status')
AGI_AUTONOMY_CYCLES = Counter('sahiix_agi_autonomy_cycles_total', 'Autonomous cycles completed')
AGI_ECOSYSTEM_NODES = Gauge('sahiix_agi_ecosystem_nodes', 'Ecosystem nodes', ['node_name', 'status'])
AGI_LLM_LATENCY = Histogram('sahiix_agi_llm_latency_seconds', 'LLM response latency', ['provider'])
AGI_LLM_ERRORS = Counter('sahiix_agi_llm_errors_total', 'LLM errors', ['provider', 'error_type'])
ECOSYSTEM_CPU_PERCENT = Gauge('sahiix_ecosystem_cpu_percent', 'CPU usage percent')
ECOSYSTEM_RAM_USED_GB = Gauge('sahiix_ecosystem_ram_used_gb', 'RAM used in GB')
ECOSYSTEM_DISK_USED_GB = Gauge('sahiix_ecosystem_disk_used_gb', 'Disk used in GB')

# Agent-specific inference counters
AGENT_INFERENCES_TOTAL = Counter('sahiix_agent_inferences_total', 'Inferences per agent', ['agent_name'])

async def update_system_metrics():
    """Background task to update system-level metrics."""
    while True:
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            ECOSYSTEM_CPU_PERCENT.set(cpu)
            ECOSYSTEM_RAM_USED_GB.set(mem.used / (1024**3))
            ECOSYSTEM_DISK_USED_GB.set(disk.used / (1024**3))
        except Exception:
            pass
        await asyncio.sleep(15)

async def update_ecosystem_metrics(agi_url: str = "http://localhost:7778"):
    """Poll AGI API and update ecosystem metrics."""
    session_timeout = aiohttp.ClientTimeout(total=5)
    while True:
        try:
            async with aiohttp.ClientSession(timeout=session_timeout) as session:
                # Agents
                async with session.get(f"{agi_url}/api/agents") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        AGI_AGENTS_TOTAL.set(len(data.get("agents", [])))
                # Tools
                async with session.get(f"{agi_url}/api/tools") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        AGI_TOOLS_TOTAL.set(len(data.get("tools", [])))
                # Memory
                async with session.get(f"{agi_url}/api/memory?limit=1") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        AGI_MEMORY_EPISODES.set(data.get("episodes", []))
                # Ecosystem
                async with session.get(f"{agi_url}/api/ecosystem/status") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for name, node in data.items():
                            status = "healthy" if node.get("healthy") else "unhealthy"
                            AGI_ECOSYSTEM_NODES.labels(node_name=name, status=status).set(1 if status == "healthy" else 0)
        except Exception:
            pass
        await asyncio.sleep(30)

async def metrics_server(host="0.0.0.0", port=9092):
    """Serve /metrics via a simple TCP socket server."""
    import socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[Metrics] Prometheus exporter on http://{host}:{port}/metrics")
    server.setblocking(False)
    loop = asyncio.get_event_loop()
    while True:
        try:
            client, addr = await loop.sock_accept(server)
            data = generate_latest(REGISTRY)
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: " + CONTENT_TYPE_LATEST.encode() + b"\r\n"
                b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                + data
            )
            await loop.sock_sendall(client, response)
            client.close()
        except Exception:
            pass
        await asyncio.sleep(0.1)

async def main():
    print("[SAHIIX AGI Metrics Exporter] Starting...")
    await asyncio.gather(
        metrics_server(),
        update_system_metrics(),
        update_ecosystem_metrics()
    )

if __name__ == "__main__":
    asyncio.run(main())
