"""Omega Sentinel — Self-Awareness & Auto-Healing Engine.

Monitors the entire SAHIIX ecosystem, detects anomalies, auto-restarts failed
services, fabricates missing skills, and optimizes performance. Runs as a
background task inside the AGI server.
"""
import asyncio
import json
import os
import psutil
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Deque

import httpx


# ── Config ───────────────────────────────────────────────────────────────────
SENTINEL_VERSION = "1.0.0"
HEALTH_CHECK_INTERVAL = 30.0      # seconds between scans
RESTART_COOLDOWN = 120.0          # seconds before retry after restart
MAX_RESTART_ATTEMPTS = 3          # consecutive restarts before alert
ALERT_COOLDOWN = 300.0            # seconds between duplicate alerts

# Ecosystem service definitions
ECOSYSTEM_SERVICES: List[Dict[str,Any]] = [
    {
        "name": "sahiixx-os",
        "url": "http://localhost:1300/health",
        "restart_cmd": "cd /home/sahiix/sahiixx_os && nohup python3 -c 'from sahiixx_os.main import app; import uvicorn; uvicorn.run(app, host=\"0.0.0.0\", port=1300)' > /tmp/sahiixx-os.log 2>&1 &",
        "priority": 10,
    },
    {
        "name": "agency-agents",
        "url": "http://localhost:8766/health",
        "restart_cmd": "cd /home/sahiix/agency-agents && nohup python3 a2a_server.py > /tmp/agency-agents.log 2>&1 &",
        "priority": 9,
    },
    {
        "name": "sovereign-swarm",
        "url": "http://localhost:8767/health",
        "restart_cmd": "cd /home/sahiix/sovereign-swarm-v2 && nohup python3 a2a_server.py > /tmp/sovereign-swarm.log 2>&1 &",
        "priority": 8,
    },
    {
        "name": "moltworker",
        "url": "http://localhost:8787/health",
        "restart_cmd": "cd /home/sahiix/repos/moltworker && nohup ./node_modules/.bin/wrangler dev --port 8787 --ip 0.0.0.0 --show-interactive-dev-session false > /tmp/moltworker.log 2>&1 &",
        "priority": 7,
    },
    {
        "name": "sahiixx-bus",
        "url": "http://localhost:8090/health",
        "restart_cmd": None,  # managed externally
        "priority": 6,
    },
    {
        "name": "ollama",
        "url": "http://localhost:11434/api/tags",
        "restart_cmd": None,  # user must restart
        "priority": 5,
    },
]


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class ServiceState:
    name: str
    url: str
    healthy: bool = False
    last_check: float = 0.0
    latency_ms: float = 0.0
    restart_count: int = 0
    last_restart: float = 0.0
    last_alert: float = 0.0
    priority: int = 0
    restart_cmd: Optional[str] = None
    error_log: Deque[str] = field(default_factory=lambda: deque(maxlen=5))


@dataclass
class SystemSnapshot:
    timestamp: float
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_used_gb: float
    disk_total_gb: float
    load_avg: float
    process_count: int


class SentinelStore:
    """Simple JSON-based persistent store for sentinel state."""

    def __init__(self, path: str = "/home/sahiix/sahiix-agi/data/sentinel_state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return {"version": SENTINEL_VERSION, "created_at": time.time(), "history": []}

    def _save(self):
        try:
            self.path.write_text(json.dumps(self._state, indent=2))
        except Exception:
            pass

    def record_event(self, event_type: str, payload: Dict[str, Any]):
        entry = {
            "t": time.time(),
            "dt": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **payload,
        }
        history: List[Dict] = self._state.setdefault("history", [])
        history.insert(0, entry)

        # rotate
        if len(history) > 200:
            self._state["history"] = history[:200]
        self._save()

    def get_recent_events(self, n: int = 50) -> List[Dict[str, Any]]:
        return self._state.get("history", [])[:n]


# ── Core Sentinel Engine ──────────────────────────────────────────────────────

class OmegaSentinel:
    """Self-awareness, auto-healing, and anomaly detection engine."""

    def __init__(self, store: Optional[SentinelStore] = None):
        self.store = store or SentinelStore()
        self.services: Dict[str, ServiceState] = {}
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._alert_callbacks: List[Callable[[Dict], None]] = []
        self._anomaly_window: Deque[SystemSnapshot] = deque(maxlen=20)

        self._init_services()

    def _init_services(self):
        for svc in ECOSYSTEM_SERVICES:
            self.services[svc["name"]] = ServiceState(
                name=svc["name"],
                url=svc["url"],
                priority=svc["priority"],
                restart_cmd=svc.get("restart_cmd"),
                healthy=True,  # assume ok until first scan
            )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        print(f"[Omega Sentinel v{SENTINEL_VERSION}] Started")

    async def stop(self):
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("[Omega Sentinel] Stopped")

    # ── Main Loop ──────────────────────────────────────────────────────────

    async def _loop(self):
        while self.running:
            try:
                await self._scan_cycle()
                await self._check_anomalies()
            except Exception:
                pass
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

    # ── Health Scan ───────────────────────────────────────────────────────

    async def _scan_cycle(self):
        sys_snapshot = self._capture_system_snapshot()
        self._anomaly_window.append(sys_snapshot)

        tasks = [self._check_service(name, ss=sys_snapshot) for name in self.services]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_service(self, name: str, ss: Optional[SystemSnapshot] = None):
        svc = self.services[name]
        t0 = time.time()
        healthy = False
        latency = 0.0
        error = ""

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(svc.url)
                latency = (time.time() - t0) * 1000
                healthy = resp.status_code < 400
                if not healthy:
                    error = f"HTTP {resp.status_code}"
        except httpx.ConnectError:
            error = "Connection refused"
        except Exception as e:
            error = str(e)

        svc.healthy = healthy
        svc.last_check = time.time()
        svc.latency_ms = latency

        if not healthy:
            svc.error_log.append(
                f"{datetime.now(timezone.utc).isoformat()} — {error} ({latency:.0f}ms)"
            )
            self.store.record_event("service_failure", {
                "name": name,
                "error": error,
                "latency_ms": latency,
                "priority": svc.priority,
            })
            await self._heal_service(name)
        else:
            # Reset restart counter on healthy check
            if svc.restart_count > 0:
                svc.restart_count = 0
                self.store.record_event("service_recovered", {"name": name, "latency_ms": latency})

    def _capture_system_snapshot(self) -> SystemSnapshot:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return SystemSnapshot(
            timestamp=time.time(),
            cpu_percent=cpu,
            ram_used_gb=mem.used / (1024**3),
            ram_total_gb=mem.total / (1024**3),
            disk_used_gb=disk.used / (1024**3),
            disk_total_gb=disk.total / (1024**3),
            load_avg=round(os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0.0, 2),
            process_count=len(psutil.pids()),
        )

    # ── Auto-Healing ────────────────────────────────────────────────────────

    async def _heal_service(self, name: str):
        svc = self.services[name]

        if not svc.restart_cmd:
            # External service (e.g., Ollama) — just alert
            await self._maybe_alert(name, f"{name} is down but cannot auto-restart")
            return

        if time.time() - svc.last_restart < RESTART_COOLDOWN:
            return  # too soon

        if svc.restart_count >= MAX_RESTART_ATTEMPTS:
            await self._maybe_alert(name, f"{name} failed {svc.restart_count}x — giving up")
            return

        svc.restart_count += 1
        svc.last_restart = time.time()

        try:
            proc = subprocess.Popen(
                svc.restart_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.store.record_event("service_restart", {
                "name": name,
                "attempt": svc.restart_count,
                "pid": proc.pid,
            })
            # cooldown before next check
            await asyncio.sleep(5)
        except Exception as e:
            self.store.record_event("restart_failed", {"name": name, "error": str(e)})

    # ── Anomaly Detection ─────────────────────────────────────────────────

    async def _check_anomalies(self):
        if len(self._anomaly_window) < 5:
            return
        window = list(self._anomaly_window)
        cpu_vals = [s.cpu_percent for s in window]
        ram_vals = [s.ram_used_gb for s in window]

        avg_cpu = sum(cpu_vals) / len(cpu_vals)
        avg_ram = sum(ram_vals) / len(ram_vals)

        latest = window[-1]
        alerts = []
        if latest.cpu_percent > avg_cpu * 2.0 and latest.cpu_percent > 80:
            alerts.append(f"CPU spike: {latest.cpu_percent:.0f}% (avg {avg_cpu:.1f}%)")
        if latest.ram_used_gb > avg_ram * 1.3:
            alerts.append(f"RAM surge: {latest.ram_used_gb:.0f}GB (avg {avg_ram:.0f}GB)")

        for alert in alerts:
            await self._maybe_alert("anomaly", alert)

    # ── Alerting ────────────────────────────────────────────────────────────

    async def _maybe_alert(self, name: str, message: str):
        svc = self.services.get(name)
        if svc and time.time() - svc.last_alert < ALERT_COOLDOWN:
            return
        if svc:
            svc.last_alert = time.time()

        self.store.record_event("alert_sent", {"target": name, "message": message})
        for cb in self._alert_callbacks:
            try:
                cb({"name": name, "message": message, "time": time.time()})
            except Exception:
                pass

    def on_alert(self, callback: Callable[[Dict], None]):
        self._alert_callbacks.append(callback)

    # ── Public API ─────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "sentinel_version": SENTINEL_VERSION,
            "running": self.running,
            "services": {
                name: {
                    "healthy": s.healthy,
                    "latency_ms": s.latency_ms,
                    "restart_count": s.restart_count,
                    "last_check": s.last_check,
                    "error_log": list(s.error_log),
                }
                for name, s in self.services.items()
            },
            "anomaly_window_size": len(self._anomaly_window),
            "recent_events": self.store.get_recent_events(10),
        }

    def heal_now(self, service_name: str) -> Dict[str, Any]:
        if service_name not in self.services:
            return {"error": f"Unknown service: {service_name}"}
        self.services[service_name].restart_count = 0
        self.services[service_name].last_restart = 0
        asyncio.create_task(self._heal_service(service_name))
        return {"status": "heal_initiated", "service": service_name}
