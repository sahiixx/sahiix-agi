"""Advanced Autonomous Engine — goal-driven observation loop with safe tool fabrication and evolution."""
import asyncio
import ast
import json
import os
import subprocess
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable


# ── Safety Proxy ─────────────────────────────────────────────────────────────

class SafetyProxy:
    """Lightweight safety scanner for mission goals and tool outputs."""

    BLOCKED_PATTERNS = [
        "rm -rf /", "rm -rf ~", "dd if=/dev/zero", ":(){ :|:& };:",
        "> /dev/sda", "mkfs.", "shutdown", "reboot", "halt",
        "os.system", "os.popen", "os.remove", "shutil.rmtree", "ctypes",
        "eval(", "exec(", "__import__(", "compile(",
    ]

    DANGEROUS_TOOLS = ["shell", "python_exec"]

    def scan(self, text: str, identity: str = "anonymous") -> dict:
        violations = []
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in text:
                violations.append(pattern)
        threat_level = "none"
        if violations:
            threat_level = "critical" if any(v in text for v in ["rm -rf /", ":(){ :|:& };:"]) else "warning"
        safe = len(violations) == 0
        return {
            "safe": safe,
            "threat_level": threat_level,
            "violations": violations,
            "sanitized": text[:500],
            "recommendation": "BLOCK" if not safe else "PROCEED",
            "identity": identity,
        }


# ── Event Bus ─────────────────────────────────────────────────────────────────

@dataclass
class Event:
    type: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source: str = "autonomy"


class EventBus:
    def __init__(self, db_path: str = "/home/sahiix/sahiix-agi/data/events.db"):
        self._history: List[Event] = []
        self._max_history = 500
        self._callbacks: Dict[str, List[Callable]] = {}
        self._webhooks: Dict[str, List[str]] = {}
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_task = asyncio.create_task(self._init_db())

    async def _init_db(self):
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    source TEXT,
                    event_type TEXT,
                    payload TEXT,
                    dispatched INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    webhook_url TEXT,
                    created_at REAL
                )
            """)
            await db.commit()

    def subscribe(self, event_type: str, callback: Callable):
        self._callbacks.setdefault(event_type, []).append(callback)

    async def add_webhook(self, event_type: str, webhook_url: str):
        await self._init_task
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO subscriptions (event_type, webhook_url, created_at) VALUES (?, ?, ?)",
                (event_type, webhook_url, time.time())
            )
            await db.commit()

    async def remove_webhook(self, event_type: str, webhook_url: str):
        await self._init_task
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM subscriptions WHERE event_type = ? AND webhook_url = ?",
                (event_type, webhook_url)
            )
            await db.commit()

    async def publish(self, event: Event):
        await self._init_task
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Persist to SQLite
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO events (timestamp, source, event_type, payload) VALUES (?, ?, ?, ?)",
                (event.timestamp, event.source, event.type, json.dumps(event.payload))
            )
            await db.commit()
            event_id = cursor.lastrowid

        # In-process callbacks
        for cb in self._callbacks.get(event.type, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception:
                pass

        # Webhook dispatch
        await self._dispatch_webhooks(event)

        # Mark dispatched
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE events SET dispatched = 1 WHERE id = ?", (event_id,))
            await db.commit()

    async def _dispatch_webhooks(self, event: Event):
        await self._init_task
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT webhook_url FROM subscriptions WHERE event_type = ?", (event.type,)
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            url = row["webhook_url"]
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await session.post(url, json={
                        "event_type": event.type,
                        "source": event.source,
                        "timestamp": event.timestamp,
                        "payload": event.payload,
                    })
            except Exception:
                pass

    def get_history(self, event_type: str = None, limit: int = 50) -> List[Event]:
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    async def get_events(self, event_type: str = "", limit: int = 50, since: float = 0) -> List[dict]:
        await self._init_task
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if event_type:
                cursor = await db.execute(
                    "SELECT * FROM events WHERE event_type = ? AND timestamp > ? ORDER BY id DESC LIMIT ?",
                    (event_type, since, limit)
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM events WHERE timestamp > ? ORDER BY id DESC LIMIT ?",
                    (since, limit)
                )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "source": r["source"],
                    "event_type": r["event_type"],
                    "payload": json.loads(r["payload"]),
                    "dispatched": bool(r["dispatched"]),
                }
                for r in rows
            ]

    async def get_subscriptions(self) -> List[dict]:
        await self._init_task
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM subscriptions ORDER BY id DESC")
            rows = await cursor.fetchall()
            return [
                {"id": r["id"], "event_type": r["event_type"],
                 "webhook_url": r["webhook_url"], "created_at": r["created_at"]}
                for r in rows
            ]


# ── Exploration Ledger ────────────────────────────────────────────────────────

LEDGER_PATH = Path(__file__).parent.parent / "data" / "exploration_ledger.json"
LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_TOPICS = [
    "Latest MCP (Model Context Protocol) updates and implementations",
    "New multi-agent orchestration frameworks and patterns",
    "Advanced RAG: chunking, routing, re-ranking, and hybrid search",
    "LLM evaluation benchmarks and safety alignment research",
    "Vector database performance comparisons and embeddings optimization",
    "Edge AI and on-device inference optimizations",
    "AI coding assistants: system prompts and tool-use patterns",
    "Self-hosted AI infrastructure: Ollama, vLLM, TGI best practices",
    "Cross-system agent communication protocols (A2A, MCP, MCP-over-HTTP)",
    "Autonomous agent safety: sandboxing, oversight, and human-in-the-loop",
]


def _load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text())
        except Exception:
            pass
    return {"explorations": [], "topics": {}, "version": "2.0"}


def _save_ledger(ledger: dict):
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))


# ── Tool Fabricator ───────────────────────────────────────────────────────────

SYNTHESIS_DIR = Path(__file__).parent.parent / "data" / "synthesized_tools"
SYNTHESIS_DIR.mkdir(parents=True, exist_ok=True)
META_FILE = SYNTHESIS_DIR / ".tool_registry.json"

_fabricated_tools: Dict[str, Callable] = {}
_fabricated_meta: List[dict] = []


def _load_fabricated_meta() -> List[dict]:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return []


def _save_fabricated_meta(meta: List[dict]):
    META_FILE.write_text(json.dumps(meta, indent=2))


class ToolFabricator:
    """Runtime tool synthesis using the native LLMManager."""

    def __init__(self, llm_manager):
        self.llm = llm_manager
        self.meta = _load_fabricated_meta()

    async def fabricate(self, name: str, description: str, requirements: str) -> Optional[Callable]:
        system_prompt = textwrap.dedent("""\
        You are a Tool Fabricator. Generate a Python async function for SAHIIX AGI.

        Rules:
        1. Output ONLY Python code, no explanations or markdown fences
        2. Function must be named exactly as requested
        3. Must have a clear docstring with Args and Returns
        4. Must handle errors gracefully with try/except
        5. Must import all needed modules inside the function or at top
        6. Must use type hints
        7. Must be self-contained (stdlib + aiohttp if needed)
        8. Timeout all network calls at 10 seconds
        9. Return a string result
        10. Function signature must accept **kwargs for flexibility
        11. Must NOT use: os.system, subprocess, eval, exec, __import__, shutil.rmtree, open, socket

        Example:
        async def ping_url(url: str = "https://example.com") -> str:
            \"\"\"Ping a URL and return HTTP status.\"\"\"
            import aiohttp
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        return f"Status: {r.status}"
            except Exception as e:
                return f"Error: {e}"
        """)

        user_prompt = f"Generate tool '{name}':\nDescription: {description}\nRequirements: {requirements}\n\nOutput ONLY Python code."

        from core.llm import Message
        messages = [Message("system", system_prompt), Message("user", user_prompt)]
        response = await self.llm.chat(messages, temperature=0.2)
        code = response.content.strip()

        # Clean markdown fences and normalize unicode quotes
        if code.startswith("```"):
            # Handle ```python, ```py, etc.
            first_line, _, rest = code.partition("\n")
            code = rest
        if code.endswith("```"):
            code = code.rsplit("```", 1)[0]
        code = code.strip()

        # Normalize smart quotes to straight quotes for AST parsing
        code = code.replace('\u201c', '"').replace('\u201d', '"').replace('\u2018', "'").replace('\u2019', "'")
        code = code.replace('\u2014', '--').replace('\u2013', '-')

        valid, msg = self._validate(code)
        if not valid:
            raise ValueError(f"Tool validation failed: {msg}")

        tool_path = SYNTHESIS_DIR / f"{name}.py"
        tool_path.write_text(code)

        namespace: dict = {}
        exec(code, namespace)
        fn = namespace.get(name)
        if not fn:
            for obj in namespace.values():
                if asyncio.iscoroutinefunction(obj) and hasattr(obj, "__name__"):
                    fn = obj
                    break
        if not fn:
            raise ValueError("Could not find function in generated code")

        _fabricated_tools[name] = fn
        m = {
            "name": name,
            "description": description,
            "created": datetime.utcnow().isoformat(),
            "path": str(tool_path)
        }
        self.meta.append(m)
        _save_fabricated_meta(self.meta)
        return fn

    def _validate(self, code: str) -> tuple[bool, str]:
        dangerous = [
            "os.system(", "os.popen(", "subprocess.call", "subprocess.Popen",
            "shutil.rmtree", "eval(", "exec(", "__import__(", "compile(",
            "open(", "socket.", "urllib.request"
        ]
        for p in dangerous:
            if p in code:
                return False, f"Dangerous pattern: {p}"
        try:
            ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        return True, "OK"

    def list_tools(self) -> List[dict]:
        return self.meta

    def get_tool(self, name: str) -> Optional[Callable]:
        if name in _fabricated_tools:
            return _fabricated_tools[name]
        for m in self.meta:
            if m["name"] == name:
                path = Path(m["path"])
                if path.exists():
                    namespace: dict = {}
                    exec(path.read_text(), namespace)
                    for obj in namespace.values():
                        if asyncio.iscoroutinefunction(obj):
                            _fabricated_tools[name] = obj
                            return obj
        return None


# ── Autonomous Engine ─────────────────────────────────────────────────────────

class AutonomousEngine:
    """Self-directed OODA loop with exploration, fabrication, and evolution."""

    def __init__(self, director, interval: int = 300):
        self.director = director
        self.interval = interval
        self.running = False
        self.bus = EventBus()
        self.safety = SafetyProxy()
        self.fabricator = ToolFabricator(director.llm)
        self._task: Optional[asyncio.Task] = None
        self._cycle_count = 0

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        await self.bus.publish(Event("autonomy.started", {"message": "Engine started"}))

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while self.running:
            try:
                await self._cycle()
            except Exception as e:
                await self.bus.publish(Event("autonomy.error", {"error": str(e)}))
            await asyncio.sleep(self.interval)

    async def _cycle(self):
        self._cycle_count += 1
        ledger = _load_ledger()

        # Cap ledger size
        max_explorations = self.director.config.get("autonomy", {}).get("max_explorations", 50)
        if len(ledger.get("explorations", [])) >= max_explorations:
            # Switch to maintenance mode: prune old entries instead of exploring
            ledger["explorations"] = ledger["explorations"][-max_explorations // 2:]

        topic = self._select_topic(ledger)
        await self.bus.publish(Event("autonomy.explore_start", {"topic": topic}))

        # Safety scan
        scan = self.safety.scan(topic)
        if not scan["safe"]:
            await self.bus.publish(Event("autonomy.safety_block", {"topic": topic, "reason": scan["violations"]}))
            return

        # Explore via web_search
        findings = []
        try:
            result = await self.director.tools.execute("web_search", query=topic, max_results=5)
            if result.success:
                findings = result.output.split("\n\n")[:5]
        except Exception:
            pass

        # Synthesize via researcher with timeout protection
        synthesis = ""
        if findings:
            prompt = (
                f"Explore topic: {topic}\n\nWeb findings:\n"
                + "\n".join(findings[:5])
                + "\n\nSynthesize a brief report with key insights and integration opportunities."
            )
            try:
                # Use asyncio.wait_for to prevent hanging the autonomy loop
                synthesis = await asyncio.wait_for(
                    self.director.chat(prompt, agent_name="researcher"),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                synthesis = "[Synthesis timeout — LLM did not respond in time]"
            except Exception:
                synthesis = "\n".join(findings[:3])

        # Record in ledger
        topics = ledger.get("topics", {})
        info = topics.get(topic, {"count": 0, "last": 0})
        info["count"] += 1
        info["last"] = time.time()
        # Truncate synthesis if it's an error to avoid polluting ledger
        if synthesis.startswith("[LLM Error") or synthesis.startswith("[Synthesis timeout"):
            info["last_synthesis"] = synthesis[:120]
            info["last_error"] = True
        else:
            info["last_synthesis"] = synthesis[:500]
            info["last_error"] = False
        topics[topic] = info
        ledger["topics"] = topics
        ledger["explorations"].append({
            "topic": topic,
            "timestamp": datetime.utcnow().isoformat(),
            "findings": len(findings)
        })
        _save_ledger(ledger)

        await self.bus.publish(Event("autonomy.explore_complete", {
            "topic": topic,
            "findings": len(findings),
            "synthesis_preview": synthesis[:200],
        }))

        # Fabrication suggestion
        if "tool" in synthesis.lower() or "api" in synthesis.lower():
            await self.bus.publish(Event("autonomy.fabrication_suggestion", {
                "topic": topic,
                "reason": "Exploration mentioned tools/APIs"
            }))

    def _select_topic(self, ledger: dict) -> str:
        topics = ledger.get("topics", {})
        now = time.time()
        scored = []
        for topic in DEFAULT_TOPICS:
            info = topics.get(topic, {"count": 0, "last": 0, "last_error": False})
            days_since = (now - info["last"]) / 86400 if info["last"] else 999
            # Deprioritize topics that recently errored
            error_penalty = 2.0 if info.get("last_error") else 1.0
            score = (days_since * (1 + info["count"] * 0.5)) * error_penalty
            scored.append((score, topic))
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    async def fabricate_tool(self, name: str, description: str, requirements: str) -> dict:
        scan = self.safety.scan(requirements)
        if not scan["safe"]:
            return {"success": False, "error": f"Safety blocked: {scan['violations']}"}
        try:
            fn = await self.fabricator.fabricate(name, description, requirements)
            self.director.tools.register(name, fn)
            await self.bus.publish(Event("autonomy.tool_fabricated", {"name": name, "description": description}))
            return {"success": True, "name": name, "description": description}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def evolve_agent(self, agent_name: str) -> dict:
        """Use LLM to critique and improve an agent's system prompt."""
        agent = self.director.agents.get(agent_name)
        if not agent:
            return {"success": False, "error": f"Agent '{agent_name}' not found"}

        current_prompt = agent.config.system_prompt
        prompt = f"""Critique and improve this agent system prompt. Keep the same identity and vibe, but make it sharper, clearer, and more capable.

Current prompt:
{current_prompt}

Output ONLY the improved prompt text. No explanations."""

        from core.llm import Message
        try:
            response = await self.director.llm.chat([
                Message("system", "You are a prompt engineering expert. Improve the given system prompt to be sharper, clearer, and more capable. Keep the same identity and vibe. Output ONLY the improved prompt text — no markdown fences, no explanations."),
                Message("user", prompt)
            ], temperature=0.4, max_tokens=1024)
            improved = response.content.strip()
            # Strip markdown fences if present
            if improved.startswith("```"):
                improved = improved.split("\n", 1)[1]
            if improved.endswith("```"):
                improved = improved.rsplit("```", 1)[0]
            improved = improved.strip()
            if improved.startswith("[") and improved.endswith("]"):
                return {"success": False, "error": f"LLM returned error-like response: {improved[:100]}"}
            if len(improved) < 30:
                return {"success": False, "error": f"Improvement too short ({len(improved)} chars)"}
            backup_dir = Path(__file__).parent.parent / "data" / "agent_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{agent_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
            backup_path.write_text(current_prompt)
            agent.config.system_prompt = improved
            await self.bus.publish(Event("autonomy.agent_evolved", {"agent": agent_name, "backup": str(backup_path)}))
            return {"success": True, "agent": agent_name, "old_length": len(current_prompt), "new_length": len(improved)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "cycle_count": self._cycle_count,
            "interval_seconds": self.interval,
            "fabricated_tools": len(self.fabricator.meta),
            "event_history": len(self.bus._history),
        }
