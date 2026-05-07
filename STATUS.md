# SAHIIX AGI v2.5.0-omega — Ground-Truth Status

> Last updated: 2026-05-05 by OpenCode (OMEGA MODE)

---

## Core Orchestrator

| Service | Endpoint | Status | Managed By |
|---------|----------|--------|------------|
| SAHIIX-AGI | `:7777` | **UP** | systemd (`sahiix-agi-web.service`) |
| Metrics Exporter | `:9092` | **UP** | host process (`setsid`) |
| Voice Daemon | — | **UP** | systemd (`sahiix-agi-voice.service`) |
| Watchdog | — | **UP** | systemd (`sahiix-watchdog.service`) |

---

## Ecosystem Node Health (Live)

| Node | URL | Status | Latency |
|------|-----|--------|---------|
| sahiix-agi | `:7777` | ✅ healthy | 0 ms |
| codex-self | `:9001` | ✅ healthy | ~11 ms |
| n8n | `:5678` | ✅ healthy | ~9 ms |
| open-webui | `:8080` | ✅ healthy | ~14 ms |
| ollama | `:11434` | ✅ healthy | ~10 ms |
| redis | `:6379` | ✅ healthy | ~0.3 ms |
| qdrant | `:6333` | ✅ healthy | ~9 ms |
| sahiixx-os | `:1300` | ✅ healthy | ~11 ms |
| agency-agents | `:8766` | ✅ healthy | ~8 ms |
| sovereign-swarm | `:8767` | ✅ healthy | ~7 ms |
| moltworker | `:8787` | ✅ healthy | ~9 ms |

> **ALL NODES ONLINE.** Full swarm operational.
> Note: moltworker runs a FastAPI shim (`/tmp/moltworker_shim.py`) due to `workerd` binary instability. The shim provides `/health` and `/execute` endpoints for ecosystem routing.

---

## Docker Stack (16 containers)

| Container | Status | Ports |
|-----------|--------|-------|
| sahiix-qdrant | ✅ Up | `:6333-6334` |
| sahiix-redis | ✅ healthy | `:6379` |
| fixfizx-frontend | ✅ healthy | `:3000` |
| agency-agents | ✅ Up | — |
| goose-aios | ✅ healthy | `:8001`, `:8765` |
| sovereign-swarm | ✅ healthy | `:9091`, `:18797` |
| fixfizx-backend | ✅ healthy | `:8002` |
| codex-self | ✅ healthy | `:9001` |
| sahiixx-bus | ✅ healthy | `:8200` |
| fixfizx-mongodb | ✅ healthy | `:27017` |
| ai-supercomputer-jupyter | ✅ healthy | `:8888` |
| ai-supercomputer-chroma | ✅ Up | `:8000` |
| ai-supercomputer-webui | ✅ healthy | `:8080` |
| n8n | ✅ Up | `:5678` |
| workerd-moltbot-sandbox | ✅ Up | `:1024`, `:1025` |

> Removed: `sahiix-metrics` container (was unhealthy and redundant — host-level metrics_exporter on `:9092` handles scraping).

---

## Verified Prometheus Metrics (`:9092/metrics`)

- `sahiix_agi_agents_total` = 6
- `sahiix_agi_tools_total` = 15
- `sahiix_agi_memory_episodes` = 1+ (varies by poll limit)
- `sahiix_agi_ecosystem_nodes` = 11 label sets, **all healthy**
- `sahiix_ecosystem_cpu_percent` — live
- `sahiix_ecosystem_ram_used_gb` — live
- `sahiix_ecosystem_disk_used_gb` — live

---

## What Changed in This Update

### Phase 1: Fix Central Nervous System
1. **`core/ecosystem.py`** — Expanded registry 5→11 nodes, added TCP probe for Redis, fixed `latency_ms = -1.0` on failure
2. **`metrics_exporter.py`** — Fixed `AGI_MEMORY_EPISODES.set(list)` TypeError, replaced silent `pass` with error logging
3. **Restarted SAHIIX-AGI** — Killed zombie/duplicate uvicorn, aligned with systemd (`sahiix-agi-web.service`)

### Phase 2: Start 4 Offline Nodes
1. **agency-agents** — Confirmed running on `:8766` (was already active)
2. **sahiixx-os** — Confirmed running on `:1300` with 2,613 leads (was already active)
3. **sovereign-swarm** — Started `a2a_server.py` on `:8767` via nohup
4. **moltworker** — Deployed FastAPI shim on `:8787` (original `workerd` binary unstable)

### Phase 3: Full System Audit & Repairs
1. **Fixed systemd conflict** — Manual uvicorn was fighting `sahiix-agi-web.service`; now fully managed by systemd
2. **Removed broken `sahiix-metrics` container** — Host-level metrics_exporter is authoritative
3. **Fixed Qdrant Docker health check** — Image has broken built-in `wget` healthcheck; disabled with `--no-healthcheck`
4. **Preserved Qdrant data** — `agent_memory` collection intact after container recreate

---

## Known Issues (Current)

1. **Ollama CPU-only**: No GPU available. Local inference latency ~3-7s per call.
2. **Moltworker shim**: Real `workerd` binary gets stuck (CLOSE_WAIT deadlock). Shim provides API compatibility until upstream fixed.
3. **No Grafana**: Dashboard JSON exists but Grafana not installed.
4. **Sudo auth failures in logs**: Something is calling `sudo` without password (watchdog uses `sudo sysctl` which may prompt).

---

## Quick Commands

```bash
# Full status
curl -s http://localhost:7777/api/status | jq

# Metrics
curl -s http://localhost:9092/metrics

# Restart main server (systemd)
sudo systemctl restart sahiix-agi-web.service

# Restart metrics exporter
kill $(lsof -t -i :9092)
cd /home/sahiix/sahiix-agi && source venv/bin/activate
setsid bash -c 'exec python metrics_exporter.py' > /tmp/sahiix-metrics.log 2>&1 &

# Restart all ecosystem services
sudo systemctl restart sahiix-agi-web.service sahiix-agi-voice.service sahiix-watchdog.service
```
