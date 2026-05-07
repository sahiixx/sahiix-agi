"""SAHIIX AGI - Ultra-low-latency FastAPI server."""
import asyncio
import time
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Set, Optional

import uvloop
import orjson
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

# asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

import sys
sys.path.insert(0, str(Path(__file__).parent))
from core.orchestrator import Director
from core.mission import MissionRunner
from core.autonomy import AutonomousEngine, EventBus, Event
from core.ingestion import IngestionManager, router as webhook_router
from core.mcp_server import MCPServer
from core.sentinel import OmegaSentinel
from core.fabricator import SkillFabricator
from core.consolidation import MemoryConsolidator
from core.optimizer import PerformanceOptimizer

try:
    from core.temporal_engine import TemporalWorkflowEngine
except Exception:
    TemporalWorkflowEngine = None

try:
    from os.backend.router import router as os_router
except Exception:
    os_router = None

try:
    from api_intelligence_router import router as intelligence_router
except Exception:
    intelligence_router = None

try:
    from core.n8n_bridge import N8nBridge
except Exception:
    N8nBridge = None

CONFIG_PATH = Path(__file__).parent / "config" / "system.yaml"
director = None
mission_runner = None
autonomy_engine = None
omega_sentinel = None
skill_fabricator = None
memory_consolidator = None
performance_optimizer = None
temporal_engine = None
n8n_bridge = None
active_websockets: Set[WebSocket] = set()
ingestion_manager: Optional[IngestionManager] = None
mcp_server: Optional[MCPServer] = None


def _ensure_director() -> Director:
    """Lazily initialize director if lifespan hasn't run (e.g., in tests)."""
    global director
    if director is None:
        director = Director(str(CONFIG_PATH))
    return director

import psutil
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client.registry import REGISTRY

# ── Prometheus Metrics ──────────────────────────────────────────────────
AGI_REQUESTS_TOTAL = Counter('sahiix_agi_requests_total', 'Total API requests', ['method', 'endpoint', 'status'])
AGI_REQUEST_LATENCY = Histogram('sahiix_agi_request_latency_seconds', 'Request latency', ['endpoint'])
AGI_ACTIVE_WS = Gauge('sahiix_agi_active_websockets', 'Active WebSocket connections')
AGI_AGENTS_TOTAL = Gauge('sahiix_agi_agents_total', 'Number of registered agents')
AGI_TOOLS_TOTAL = Gauge('sahiix_agi_tools_total', 'Number of registered tools')
AGI_MEMORY_EPISODES = Gauge('sahiix_agi_memory_episodes', 'Total memory episodes')
AGI_AUTONOMY_ENABLED = Gauge('sahiix_agi_autonomy_enabled', 'Autonomy engine status')
AGI_ECOSYSTEM_NODES = Gauge('sahiix_agi_ecosystem_nodes', 'Ecosystem nodes', ['node_name', 'status'])
ECOSYSTEM_CPU_PERCENT = Gauge('sahiix_ecosystem_cpu_percent', 'CPU usage percent')
ECOSYSTEM_RAM_USED_GB = Gauge('sahiix_ecosystem_ram_used_gb', 'RAM used in GB')
ECOSYSTEM_DISK_USED_GB = Gauge('sahiix_ecosystem_disk_used_gb', 'Disk used in GB')

async def update_system_metrics():
    """Background task: update system-level metrics every 15s."""
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

async def update_agi_metrics():
    """Background task: update AGI-specific metrics every 30s."""
    while True:
        try:
            if director and director.agents:
                AGI_AGENTS_TOTAL.set(len(director.agents))
                AGI_TOOLS_TOTAL.set(len(director.tools.list_tools()) if hasattr(director.tools, 'list_tools') else 0)
                AGI_ACTIVE_WS.set(len(active_websockets))
            if autonomy_engine:
                AGI_AUTONOMY_ENABLED.set(1 if autonomy_engine.running else 0)
        except Exception:
            pass
        await asyncio.sleep(30)


class ORJSONResponse(JSONResponse):
    media_type = "application/json"
    def render(self, content) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global director
    director = Director(str(CONFIG_PATH))
    await director.warmup()
    global mission_runner
    mission_runner = MissionRunner(director, broadcast_fn=broadcast_to_websockets)
    global autonomy_engine
    autonomy_config = director.config.get("autonomy", {})
    if autonomy_config.get("enabled", True):
        autonomy_engine = AutonomousEngine(director, interval=autonomy_config.get("interval_seconds", 120))
        await autonomy_engine.start()
        print("[SAHIIX AGI] Autonomy engine started.")
    else:
        autonomy_engine = None

    # Initialize ingestion manager and wire event bus + optional kafka
    global ingestion_manager
    ingestion_cfg = director.config.get("ingestion", {})
    kafka_cfg = ingestion_cfg.get("kafka")
    ingestion_manager = IngestionManager(bus=autonomy_engine.bus if autonomy_engine else EventBus(), kafka_config=kafka_cfg)
    app.state.event_bus = ingestion_manager.bus
    app.state.kafka_publisher = ingestion_manager.kafka
    await ingestion_manager.start()
    print("[SAHIIX AGI] Ingestion manager started.")

    # Start MCP server in background if enabled
    global mcp_server
    mcp_cfg = director.config.get("mcp", {})
    if mcp_cfg.get("enabled", True):
        mcp_server = MCPServer(director.tools)
        mcp_server.start_background()
        print("[SAHIIX AGI] MCP server started on stdio.")

    # Wire Redis cache for LLM L2 if container is reachable
    try:
        from core.redis_cache import AsyncRedisCache
        redis_cache = AsyncRedisCache(url="redis://localhost:6379")
        await redis_cache.connect()
        for provider in director.llm.providers.values():
            if hasattr(provider, '_redis'):
                provider._redis = redis_cache
        app.state.redis = redis_cache
        print("[SAHIIX AGI] Redis cache wired to LLM providers.")
    except Exception as e:
        print(f"[SAHIIX AGI] Redis not wired: {e}")

    # Wire Qdrant vector DB if available
    try:
        from core.vector_db import QdrantVectorDB
        qdrant = QdrantVectorDB()
        await qdrant.connect()
        app.state.qdrant = qdrant
        # Ensure agent_memory collection exists
        await qdrant.create_collection("agent_memory", dim=384, distance="Cosine")
        app.state.vector_db = qdrant
        print("[SAHIIX AGI] Qdrant vector DB wired.")
    except Exception as e:
        print(f"[SAHIIX AGI] Qdrant not wired: {e}")

    # Start Omega Sentinel — self-awareness & auto-healing
    global omega_sentinel
    omega_sentinel = OmegaSentinel()
    await omega_sentinel.start()
    app.state.sentinel = omega_sentinel
    print("[SAHIIX AGI] Omega Sentinel (self-awareness + auto-heal) started.")

    # Skill Fabricator — auto-detects missing skills
    global skill_fabricator
    skill_fabricator = SkillFabricator()
    app.state.fabricator = skill_fabricator
    print("[SAHIIX AGI] Skill Fabricator ready.")

    # Memory Consolidator — compress old episodes into knowledge
    global memory_consolidator
    memory_consolidator = MemoryConsolidator()
    app.state.consolidator = memory_consolidator
    print("[SAHIIX AGI] Memory Consolidator ready.")

    # Performance Optimizer — latency/cost/model selection
    global performance_optimizer
    performance_optimizer = PerformanceOptimizer()
    app.state.optimizer = performance_optimizer
    print("[SAHIIX AGI] Performance Optimizer ready.")

    # Start background metrics collectors
    asyncio.create_task(update_system_metrics())
    asyncio.create_task(update_agi_metrics())
    print("[SAHIIX AGI] Warmed up. Ready.")
    yield
    for ws in active_websockets:
        try:
            await ws.close()
        except Exception:
            pass
    if temporal_engine:
        await temporal_engine.close()
    if n8n_bridge:
        await n8n_bridge.close()
    if autonomy_engine:
        await autonomy_engine.stop()
    if ingestion_manager:
        await ingestion_manager.stop()
    if mcp_server:
        mcp_server.stop()
    if omega_sentinel:
        await omega_sentinel.stop()
    # Disconnect Redis
    if hasattr(app.state, 'redis') and app.state.redis:
        try:
            await app.state.redis.disconnect()
        except Exception:
            pass
    # Disconnect Qdrant
    if hasattr(app.state, 'qdrant') and app.state.qdrant:
        try:
            await app.state.qdrant.disconnect()
        except Exception:
            pass
    await director.close()

app = FastAPI(title="SAHIIX AGI", version="1.0.0-rt", default_response_class=ORJSONResponse, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=100)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Count requests and measure latency for Prometheus."""
    start = time.monotonic()
    response = await call_next(request)
    latency = time.monotonic() - start
    endpoint = request.url.path
    method = request.method
    status = str(response.status_code)
    AGI_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
    AGI_REQUEST_LATENCY.labels(endpoint=endpoint).observe(latency)
    return response



async def broadcast_to_websockets(data: dict):
    disconnected = []
    for ws in active_websockets:
        try:
            await ws.send_json(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        active_websockets.discard(ws)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "SAHIIX AGI ready"})
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "chat")
            if msg_type == "chat":
                start = time.monotonic()
                response = await _ensure_director().chat(data.get("message", ""), agent_name=data.get("agent"))
                await websocket.send_json({"type": "response", "response": response, "agent": data.get("agent") or "auto", "latency_ms": round((time.monotonic() - start) * 1000, 2)})
            elif msg_type == "stream":
                await websocket.send_json({"type": "stream_start"})
                async for chunk in _ensure_director().stream_chat(data.get("message", ""), agent_name=data.get("agent")):
                    await websocket.send_json({"type": "stream_chunk", "chunk": chunk})
                await websocket.send_json({"type": "stream_end"})
            elif msg_type == "parallel":
                start = time.monotonic()
                results = await _ensure_director().parallel_chat(data.get("message", ""), agents=data.get("agents", []))
                await websocket.send_json({"type": "parallel_response", "results": results, "latency_ms": round((time.monotonic() - start) * 1000, 2)})
            elif msg_type == "tool":
                result = await _ensure_director().tools.execute(data.get("name", ""), **data.get("params", {}))
                await websocket.send_json({"type": "tool_result", "success": result.success, "output": result.output, "error": result.error, "latency_ms": round(result.latency_ms, 2)})
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "time": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(websocket)

@app.get("/", response_class=HTMLResponse)
async def root():
    with open(Path(__file__).parent / "ui" / "voice.html") as f:
        return HTMLResponse(f.read())

@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    with open(Path(__file__).parent / "ui" / "chat.html") as f:
        return HTMLResponse(f.read())

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    with open(Path(__file__).parent / "ui" / "dashboard.html") as f:
        return HTMLResponse(f.read())

async def _check_intelligence_health() -> dict:
    """Check if the intelligence API on :8082 is reachable."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            resp = await client.get("http://localhost:8082/health")
            return {
                "reachable": True,
                "status": "healthy" if resp.status_code == 200 else "degraded",
                "status_code": resp.status_code,
            }
    except Exception as exc:
        return {"reachable": False, "status": "unreachable", "error": str(exc)}


@app.get("/api/health")
async def health():
    """Lightweight health check with intelligence API reachability."""
    intelligence = await _check_intelligence_health()
    return {
        "status": "ok",
        "version": _ensure_director().config.get("system", {}).get("version", "unknown"),
        "agents": list(_ensure_director().agents.keys()),
        "intelligence_api": intelligence,
        "timestamp": time.time(),
    }

@app.get("/api/status")
async def status():
    start = time.monotonic()
    s = await _ensure_director().get_full_status()
    s["api_latency_ms"] = round((time.monotonic() - start) * 1000, 3)
    return s

@app.get("/api/metrics")
async def metrics():
    result = await _ensure_director().tools.execute("performance_metrics")
    return {"metrics": json.loads(result.output) if result.success else {}, "timestamp": time.time()}

@app.post("/api/chat")
async def chat(request: Request):
    data = orjson.loads(await request.body())
    start = time.monotonic()
    response = await _ensure_director().chat(data.get("message", ""), agent_name=data.get("agent"))
    return {"response": response, "agent": data.get("agent") or "auto", "latency_ms": round((time.monotonic() - start) * 1000, 2)}

@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    data = orjson.loads(await request.body())
    async def event_generator():
        try:
            async for chunk in _ensure_director().stream_chat(data.get("message", ""), agent_name=data.get("agent")):
                yield f"data: {orjson.dumps({'chunk': chunk}).decode()}\n\n"
            yield f"data: {orjson.dumps({'done': True}).decode()}\n\n"
        except Exception as e:
            yield f"data: {orjson.dumps({'error': str(e)}).decode()}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/chat/parallel")
async def chat_parallel(request: Request):
    data = orjson.loads(await request.body())
    start = time.monotonic()
    results = await _ensure_director().parallel_chat(data.get("message", ""), agents=data.get("agents", []))
    return {"results": results, "latency_ms": round((time.monotonic() - start) * 1000, 2)}

@app.post("/api/chat/memory")
async def chat_with_memory(request: Request):
    """Chat with RAG memory retrieval from Qdrant vector DB."""
    data = orjson.loads(await request.body())
    user_message = data.get("message", "")
    agent_name = data.get("agent", "director")
    
    # 1) Retrieve relevant memories from Qdrant via vector search
    memories = []
    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant:
        try:
            # Get embedding for the query via LLM provider
            embed_model = _ensure_director().llm.default_provider
            query_vec = await embed_model.embed(user_message)
            # Fallback if embed returns empty
            if not query_vec:
                import random
                random.seed(hash(user_message) % 2**31)
                query_vec = [random.random() for _ in range(384)]
            
            results = await qdrant.search("agent_memory", query_vec, limit=5)
            for r in results:
                payload = r.get("payload", {})
                score = r.get("score", 0)
                # Lower threshold to include more context
                if score > 0.3:
                    role = payload.get('role', '?')
                    text = payload.get('text', '')
                    ts = payload.get('ts', '')
                    memories.append(f"[{role} @ {ts}] {text}")
        except Exception:
            pass
    
    # 2) Augment prompt with retrieved memories
    context = "\n".join(memories) if memories else ""
    augmented = user_message
    if context:
        augmented = f"[Context from memory]\n{context}\n\n[User] {user_message}"
    
    # 3) Send to agent
    start = time.monotonic()
    response = await _ensure_director().chat(augmented, agent_name=agent_name)
    return {
        "response": response,
        "agent": agent_name,
        "memory_chunks_retrieved": len(memories),
        "latency_ms": round((time.monotonic() - start) * 1000, 2),
    }

@app.get("/api/agents")
async def list_agents():
    return {"agents": list(_ensure_director().agents.keys())}

@app.get("/api/tools")
async def list_tools():
    return {"tools": _ensure_director().tools.list_tools()}

@app.get("/api/mcp/tools")
async def mcp_tools_list():
    """MCP-compatible tools listing from tools.registry."""
    tools = []
    for t in _ensure_director().tools.list_tools():
        tools.append({
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "inputSchema": {"type": "object", "properties": {}},
        })
    return {"tools": tools}

@app.post("/api/tool")
async def exec_tool(request: Request):
    data = orjson.loads(await request.body())
    result = await _ensure_director().tools.execute(data.get("name", ""), **data.get("params", {}))
    return {"success": result.success, "output": result.output, "error": result.error, "latency_ms": round(result.latency_ms, 2)}

@app.get("/api/memory")
async def get_memory(agent: str = None, limit: int = 20):
    episodes = await _ensure_director().memory.get_recent(agent=agent, limit=limit)
    return {"episodes": episodes}

@app.post("/api/memory/search")
async def search_memory(request: Request):
    data = orjson.loads(await request.body())
    query = data.get("query", "")
    mode = data.get("mode", "keyword")  # keyword or vector
    agent = data.get("agent")
    limit = data.get("limit", 10)
    if mode == "vector":
        vec = await _ensure_director().llm.embed(query)
        if vec:
            episodes = await _ensure_director().memory.vector_search(vec, limit=limit, agent=agent)
        else:
            episodes = []
    else:
        episodes = await _ensure_director().memory.search(query, limit=limit)
    return {"mode": mode, "query": query, "episodes": episodes}

@app.post("/api/agents/delegate")
async def delegate_task(request: Request):
    data = orjson.loads(await request.body())
    from_agent = data.get("from", "director")
    to_agent = data.get("to", "coder")
    task = data.get("task", "")
    context = data.get("context", "")
    result = await _ensure_director().delegate(from_agent, to_agent, task, context)
    return {"from": from_agent, "to": to_agent, "result": result}

@app.post("/api/broadcast")
async def broadcast(request: Request):
    data = orjson.loads(await request.body())
    disconnected = []
    for ws in active_websockets:
        try:
            await ws.send_json({"type": "broadcast", "message": data.get("message", "")})
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        active_websockets.discard(ws)
    return {"sent_to": len(active_websockets)}


@app.post("/api/mission")
async def create_mission(request: Request):
    data = orjson.loads(await request.body())
    goal = data.get("goal", "")
    mission = await mission_runner.create_mission(goal)
    asyncio.create_task(mission_runner.run_mission(mission.id))
    return {"mission_id": mission.id, "goal": mission.goal, "steps": len(mission.steps)}

@app.get("/api/mission/{mission_id}")
async def get_mission(mission_id: str):
    mission = mission_runner.get_mission(mission_id)
    if not mission:
        return JSONResponse({"error": "Mission not found"}, status_code=404)
    return mission.to_dict()

@app.get("/api/missions")
async def list_missions():
    return {"missions": mission_runner.list_missions()}

# ── Ecosystem Unification Endpoints ───────────────────────────────────────────

@app.get("/api/ecosystem/status")
async def ecosystem_status():
    return await _ensure_director().get_ecosystem_status()

@app.post("/api/ecosystem/route")
async def ecosystem_route(request: Request):
    data = orjson.loads(await request.body())
    text = data.get("message", "")
    if not _ensure_director().router:
        return {"error": "Unified router not available"}
    result = await _ensure_director().router.route(text)
    return result

@app.post("/api/ecosystem/dispatch")
async def ecosystem_dispatch(request: Request):
    data = orjson.loads(await request.body())
    node = data.get("node", "")
    endpoint = data.get("endpoint", "/")
    payload = data.get("payload", {})
    method = data.get("method", "POST")
    if not _ensure_director().bridge:
        return {"error": "Ecosystem bridge not available"}
    return await _ensure_director().bridge.dispatch_to(node, endpoint, payload, method)

# ── Autonomy Endpoints ────────────────────────────────────────────────────────

@app.get("/api/autonomy/status")
async def autonomy_status():
    if not autonomy_engine:
        return {"enabled": False}
    return autonomy_engine.get_status()

@app.post("/api/autonomy/toggle")
async def autonomy_toggle(request: Request):
    data = orjson.loads(await request.body())
    enabled = data.get("enabled", True)
    global autonomy_engine
    if enabled:
        if not autonomy_engine or not autonomy_engine.running:
            autonomy_engine = AutonomousEngine(director, interval=_ensure_director().config.get("autonomy", {}).get("interval_seconds", 120))
            await autonomy_engine.start()
        return {"enabled": True, "status": autonomy_engine.get_status()}
    else:
        if autonomy_engine:
            await autonomy_engine.stop()
        return {"enabled": False}

@app.post("/api/autonomy/fabricate")
async def autonomy_fabricate(request: Request):
    data = orjson.loads(await request.body())
    if not autonomy_engine:
        return {"error": "Autonomy engine not running"}
    return await autonomy_engine.fabricate_tool(
        data.get("name", ""),
        data.get("description", ""),
        data.get("requirements", "")
    )

@app.post("/api/autonomy/evolve")
async def autonomy_evolve(request: Request):
    data = orjson.loads(await request.body())
    if not autonomy_engine:
        return {"error": "Autonomy engine not running"}
    return await autonomy_engine.evolve_agent(data.get("agent", "director"))

# ── Omega Sentinel Endpoints (Self-Awareness + Auto-Heal) ──────────────────

@app.get("/api/sentinel/status")
async def sentinel_status():
    if not omega_sentinel:
        return {"error": "Omega Sentinel not initialized"}
    return omega_sentinel.status()

@app.post("/api/sentinel/heal")
async def sentinel_heal(request: Request):
    data = orjson.loads(await request.body())
    if not omega_sentinel:
        return {"error": "Omega Sentinel not initialized"}
    return omega_sentinel.heal_now(data.get("service", ""))

# ── Skill Fabricator Endpoints ───────────────────────────────────────────────

@app.get("/api/fabricator/status")
async def fabricator_status():
    if not skill_fabricator:
        return {"error": "Skill Fabricator not initialized"}
    return skill_fabricator.status()

@app.get("/api/fabricator/skills")
async def fabricator_skills():
    if not skill_fabricator:
        return {"error": "Skill Fabricator not initialized"}
    return {"fabricated_skills": skill_fabricator.list_fabricated()}

# ── Memory Consolidation Endpoints ────────────────────────────────────────────

@app.get("/api/consolidation/status")
async def consolidation_status():
    if not memory_consolidator:
        return {"error": "Memory Consolidator not initialized"}
    return memory_consolidator.status()

@app.post("/api/consolidation/run")
async def consolidation_run(request: Request):
    data = orjson.loads(await request.body())
    if not memory_consolidator:
        return {"error": "Memory Consolidator not initialized"}
    return memory_consolidator.consolidate(keep_recent=data.get("keep_recent", 50))

@app.post("/api/consolidation/recall")
async def consolidation_recall(request: Request):
    data = orjson.loads(await request.body())
    if not memory_consolidator:
        return {"error": "Memory Consolidator not initialized"}
    return {"results": memory_consolidator.recall(data.get("query", ""), data.get("top_k", 5))}

# ── Performance Optimizer Endpoints ───────────────────────────────────────────

@app.get("/api/optimizer/status")
async def optimizer_status():
    if not performance_optimizer:
        return {"error": "Performance Optimizer not initialized"}
    return performance_optimizer.status()

@app.post("/api/optimizer/model")
async def optimizer_model(request: Request):
    data = orjson.loads(await request.body())
    if not performance_optimizer:
        return {"error": "Performance Optimizer not initialized"}
    return performance_optimizer.get_recommendation(data.get("task", ""))

# ── Intelligence Router Mount ────────────────────────────────────────────────
if intelligence_router is not None:
    app.include_router(intelligence_router, prefix="/api/intelligence")

# ── Webhook Router Mount ─────────────────────────────────────────────────────
app.include_router(webhook_router)
if os_router is not None:
    app.include_router(os_router)

# Serve SAHIIX OS frontend static files
from fastapi.staticfiles import StaticFiles
os_frontend_dist = Path(__file__).parent / "os" / "frontend" / "dist"
if os_frontend_dist.exists():
    app.mount("/os", StaticFiles(directory=str(os_frontend_dist), html=True), name="os_frontend")
    # SPA fallback for /os/* routes
    @app.get("/os/{full_path:path}", response_class=HTMLResponse)
    async def os_spa_fallback(full_path: str):
        index_file = os_frontend_dist / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text())
        return JSONResponse({"error": "OS frontend not built"}, status_code=404)


# ── Real-time Event Streaming ───────────────────────────────────────────────
@app.get("/api/events/stream")
async def events_stream():
    """SSE endpoint for real-time event streaming from the ingestion event bus."""
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        bus = ingestion_manager.bus if ingestion_manager else None
        if not bus:
            yield f"data: {orjson.dumps({'error': 'Event bus not available'}).decode()}\n\n"
            return

        async def _listener(event):
            try:
                await queue.put(event)
            except Exception:
                pass

        bus.subscribe("ingestion.rss", _listener)
        bus.subscribe("ingestion.webhook", _listener)
        bus.subscribe("ingestion.api", _listener)
        bus.subscribe("ingestion.file", _listener)

        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {orjson.dumps({'type': event.type, 'payload': event.payload, 'timestamp': event.timestamp, 'source': event.source}).decode()}\n\n"
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
        except Exception:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Unified Event Bus REST API ──────────────────────────────────────────────

def _get_event_bus():
    bus = getattr(app.state, "event_bus", None)
    if not bus and autonomy_engine:
        bus = autonomy_engine.bus
    return bus

@app.post("/api/events/publish")
async def events_publish(request: Request):
    data = orjson.loads(await request.body())
    bus = _get_event_bus()
    if not bus:
        return {"error": "Event bus not available"}
    event = Event(
        type=data.get("event_type", "generic"),
        payload=data.get("payload", {}),
        source=data.get("source", "external"),
    )
    await bus.publish(event)
    return {"ok": True, "event_type": event.type, "timestamp": event.timestamp}

@app.get("/api/events")
async def events_list(event_type: str = "", limit: int = 50, since: float = 0):
    bus = _get_event_bus()
    if not bus:
        return {"error": "Event bus not available"}
    events = await bus.get_events(event_type=event_type, limit=limit, since=since)
    return {"events": events, "count": len(events)}

@app.post("/api/events/subscribe")
async def events_subscribe(request: Request):
    data = orjson.loads(await request.body())
    bus = _get_event_bus()
    if not bus:
        return {"error": "Event bus not available"}
    await bus.add_webhook(data.get("event_type", ""), data.get("webhook_url", ""))
    return {"ok": True, "event_type": data.get("event_type"), "webhook_url": data.get("webhook_url")}

@app.get("/api/events/subscriptions")
async def events_subscriptions():
    bus = _get_event_bus()
    if not bus:
        return {"error": "Event bus not available"}
    subs = await bus.get_subscriptions()
    return {"subscriptions": subs}


# ── Ingestion Management ────────────────────────────────────────────────────
@app.get("/api/ingestion/feeds")
async def list_feeds():
    if not ingestion_manager:
        return {"feeds": []}
    return {"feeds": [
        {"url": f.url, "interval_seconds": f.interval_seconds}
        for f in ingestion_manager.rss.feeds.values()
    ]}

@app.post("/api/ingestion/feeds")
async def add_feed(request: Request):
    data = orjson.loads(await request.body())
    url = data.get("url", "")
    interval = data.get("interval_seconds", 300)
    if not ingestion_manager:
        return JSONResponse({"error": "Ingestion manager not available"}, status_code=503)
    cfg = ingestion_manager.rss.add_feed(url, interval)
    return {"url": cfg.url, "interval_seconds": cfg.interval_seconds, "status": "added"}

@app.get("/api/ingestion/pollers")
async def list_pollers():
    if not ingestion_manager:
        return {"pollers": []}
    return {"pollers": [
        {"name": p.name, "url": p.url, "interval_seconds": p.interval_seconds, "method": p.method}
        for p in ingestion_manager.poller.pollers.values()
    ]}

@app.post("/api/ingestion/pollers")
async def add_poller(request: Request):
    from core.ingestion import PollerConfig
    data = orjson.loads(await request.body())
    cfg = PollerConfig(
        name=data.get("name", ""),
        url=data.get("url", ""),
        interval_seconds=data.get("interval_seconds", 60),
        method=data.get("method", "GET"),
        headers=data.get("headers", {}),
        body=data.get("body"),
    )
    if not ingestion_manager:
        return JSONResponse({"error": "Ingestion manager not available"}, status_code=503)
    ingestion_manager.poller.add(cfg)
    return {"name": cfg.name, "url": cfg.url, "status": "added"}

# ── Temporal Workflow Endpoints ──────────────────────────────────────────────

@app.post("/api/workflows/temporal/start")
async def temporal_start(request: Request):
    global temporal_engine
    if TemporalWorkflowEngine is None:
        return JSONResponse({"error": "temporal-sdk not installed"}, status_code=503)
    if temporal_engine is None:
        temporal_engine = TemporalWorkflowEngine(_ensure_director())
        await temporal_engine.start_worker()
    data = orjson.loads(await request.body())
    mission_id = data.get("mission_id", f"mission-{int(time.time())}")
    goal = data.get("goal", "")
    wid = await temporal_engine.start_mission(mission_id, goal)
    return {"mission_id": mission_id, "workflow_id": wid, "status": "started"}

@app.get("/api/workflows/temporal/{workflow_id}")
async def temporal_status(workflow_id: str):
    if TemporalWorkflowEngine is None:
        return JSONResponse({"error": "temporal-sdk not installed"}, status_code=503)
    global temporal_engine
    if temporal_engine is None:
        temporal_engine = TemporalWorkflowEngine(_ensure_director())
        await temporal_engine.start_worker()
    info = await temporal_engine.get_mission_status(workflow_id)
    return info

@app.post("/api/workflows/temporal/{workflow_id}/cancel")
async def temporal_cancel(workflow_id: str):
    if TemporalWorkflowEngine is None:
        return JSONResponse({"error": "temporal-sdk not installed"}, status_code=503)
    global temporal_engine
    if temporal_engine is None:
        return JSONResponse({"error": "Temporal engine not initialized"}, status_code=503)
    result = await temporal_engine.cancel_mission(workflow_id)
    return result

# ── n8n Bridge Endpoints ─────────────────────────────────────────────────────

@app.post("/api/workflows/n8n/trigger")
async def n8n_trigger(request: Request):
    global n8n_bridge
    if N8nBridge is None:
        return JSONResponse({"error": "httpx not installed"}, status_code=503)
    if n8n_bridge is None:
        n8n_bridge = N8nBridge()
    data = orjson.loads(await request.body())
    webhook_url = data.get("webhook_url", "")
    payload = data.get("payload", {})
    result = await n8n_bridge.trigger_n8n(webhook_url, payload)
    return result

@app.get("/api/workflows/n8n/workflows")
async def n8n_list_workflows():
    global n8n_bridge
    if N8nBridge is None:
        return JSONResponse({"error": "httpx not installed"}, status_code=503)
    if n8n_bridge is None:
        n8n_bridge = N8nBridge()
    return await n8n_bridge.list_workflows()

@app.post("/api/workflows/n8n/register")
async def n8n_register_webhook(request: Request):
    global n8n_bridge
    if N8nBridge is None:
        return JSONResponse({"error": "httpx not installed"}, status_code=503)
    if n8n_bridge is None:
        n8n_bridge = N8nBridge()
    data = orjson.loads(await request.body())
    name = data.get("name", "")
    url = data.get("url", "")
    tool_name = data.get("tool_name")
    tool_params = data.get("tool_params", {})
    result = n8n_bridge.register_webhook(name, url, tool_name=tool_name, tool_params=tool_params)
    return result


# ── Model Management ──────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models():
    """List all configured LLM providers with their status."""
    models_info = []
    for name, provider in _ensure_director().llm.providers.items():
        healthy = provider.breaker.state != "open"
        models_info.append({
            "name": name,
            "model": provider.config.get("model", "unknown"),
            "healthy": healthy,
            "circuit_state": provider.breaker.state,
            "cache_ttl_seconds": provider.config.get("cache_ttl_seconds", 60),
        })
    return {"models": models_info, "default": _ensure_director().llm.config.get("default_provider", "ollama")}


@app.get("/api/config")
async def get_config():
    """Get runtime system configuration."""
    cfg = {
        "system": _ensure_director().config.get("system", {}),
        "llm": {
            "default_provider": _ensure_director().llm.config.get("default_provider"),
            "providers": list(_ensure_director().llm.providers.keys()),
            "fallback_chain": _ensure_director().llm.config.get("fallback_chain", []),
        },
        "agents": list(_ensure_director().agents.keys()),
        "tools": [t["name"] for t in _ensure_director().tools.list_tools()],
        "autonomy": _ensure_director().config.get("autonomy", {}),
    }
    return cfg


# ── Prometheus Metrics Endpoint ─────────────────────────────────────────────

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
from prometheus_client.registry import REGISTRY

@app.get("/metrics")
async def metrics():
    """Expose Prometheus-compatible metrics."""
    data = generate_latest(REGISTRY)
    return StreamingResponse(iter([data]), media_type=CONTENT_TYPE_LATEST)


