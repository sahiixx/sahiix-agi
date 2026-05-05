"""High-performance tool registry with async memoization, security hardening, and structured metadata."""
import asyncio
import json
import subprocess
import time
import os
import re
from typing import Dict, Any, Callable, List
from dataclasses import dataclass
from pathlib import Path

import orjson


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str = ""
    metadata: Dict[str, Any] = None
    latency_ms: float = 0.0


class ToolRegistry:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tools: Dict[str, Callable] = {}
        self._graph_data: Dict[str, Any] = {}
        self._tool_cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._register_defaults()

    async def warmup(self):
        graph_path = self.config.get("repo_knowledge", {}).get("graph_data_path", "")
        if graph_path:
            try:
                with open(graph_path, "rb") as f:
                    self._graph_data = orjson.loads(f.read())
            except Exception:
                pass

    def _cached(self, key: str, ttl: int = 30):
        if key in self._tool_cache and (time.time() - self._cache_time.get(key, 0)) < ttl:
            return self._tool_cache[key]
        return None

    def _set_cache(self, key: str, value):
        self._tool_cache[key] = value
        self._cache_time[key] = time.time()

    def _register_defaults(self):
        self.register("shell", self._shell)
        self.register("file_read", self._file_read)
        self.register("file_write", self._file_write)
        self.register("system_info", self._system_info)
        self.register("repo_knowledge", self._repo_knowledge)
        self.register("web_search", self._web_search)
        self.register("performance_metrics", self._performance_metrics)
        self.register("python_exec", self._python_exec)
        self.register("git_ops", self._git_ops)
        self.register("docker_ops", self._docker_ops)
        self.register("http_request", self._http_request)
        self.register("repo_recommend", self._repo_recommend)
        self.register("sql_analysis", self._sql_analysis)
        self.register("browser", self._browser)
        self.register("crm_query", self._crm_query)

    def register(self, name: str, fn: Callable):
        self.tools[name] = fn

    async def execute(self, name: str, **params) -> ToolResult:
        if name not in self.tools:
            return ToolResult(False, "", f"Tool '{name}' not found")
        start = time.time()
        try:
            fn = self.tools[name]
            if asyncio.iscoroutinefunction(fn):
                result = await fn(**params)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: fn(**params))
            latency = (time.time() - start) * 1000
            return result if isinstance(result, ToolResult) else ToolResult(True, str(result), latency_ms=latency)
        except Exception as e:
            return ToolResult(False, "", str(e), latency_ms=(time.time() - start) * 1000)

    def list_tools(self) -> List[Dict[str, str]]:
        return [
            {
                "name": name,
                "description": (fn.__doc__ or "No description").strip()
            }
            for name, fn in self.tools.items()
        ]

    # ── Security helpers ──────────────────────────────────────────────────────

    def _sanitize_path(self, path: str, operation: str = "read") -> Path:
        """Sanitize file paths to prevent directory traversal."""
        allowed = self.config.get("file_write", {}).get("allowed_base_dirs", ["/home/sahiix/sahiix-agi", "/tmp"])
        p = Path(path).resolve()
        for base in allowed:
            base_resolved = Path(base).resolve()
            try:
                p.relative_to(base_resolved)
                return p
            except ValueError:
                continue
        # If no base dir matched, restrict to project root for reads, deny for writes
        project_root = Path(__file__).parent.parent.resolve()
        try:
            p.relative_to(project_root)
            if operation == "read":
                return p
        except ValueError:
            pass
        raise PermissionError(f"Path '{path}' is outside allowed directories: {allowed}")

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _shell(self, command: str, timeout: int = 15, **kwargs) -> ToolResult:
        """Execute an allowed shell command safely. Params: command (str), timeout (int, default 15)."""
        allowed = self.config.get("shell", {}).get("allowed_commands", [])
        blocked = self.config.get("shell", {}).get("blocked_patterns", [])
        cmd_base = command.split()[0] if command.split() else ""
        if allowed and cmd_base not in allowed:
            return ToolResult(False, "", f"Command '{cmd_base}' not in allowed list: {allowed}")
        for pattern in blocked:
            if pattern in command:
                return ToolResult(False, "", f"Blocked dangerous pattern: '{pattern}'")
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
            return ToolResult(
                result.returncode == 0,
                result.stdout[:10000],
                result.stderr[:2000],
                {"returncode": result.returncode}
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "", f"Timeout after {timeout}s")

    def _file_read(self, path: str) -> ToolResult:
        """Read a text file safely. Params: path (str)."""
        try:
            safe_path = self._sanitize_path(path, operation="read")
            with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
                return ToolResult(True, f.read())
        except PermissionError as e:
            return ToolResult(False, "", f"Security: {e}")
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _file_write(self, path: str, content: str) -> ToolResult:
        """Write text to a file safely (restricted to allowed directories). Params: path (str), content (str)."""
        try:
            safe_path = self._sanitize_path(path, operation="write")
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(True, f"Wrote {len(content)} chars to {safe_path}")
        except PermissionError as e:
            return ToolResult(False, "", f"Security: {e}")
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _system_info(self) -> ToolResult:
        """Get system platform info. No params."""
        import platform
        info = {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "hostname": platform.node(),
            "agi_version": "2.0-rt"
        }
        return ToolResult(True, json.dumps(info, indent=2))

    def _repo_knowledge(self, query: str = "", category: str = "") -> ToolResult:
        """Query AI repo knowledge base. Params: query (str), category (str)."""
        cache_key = f"repo:{query}:{category}"
        cached = self._cached(cache_key, ttl=60)
        if cached:
            return cached

        if not self._graph_data:
            graph_path = self.config.get("repo_knowledge", {}).get("graph_data_path", "")
            if graph_path and Path(graph_path).exists():
                with open(graph_path, "rb") as f:
                    self._graph_data = orjson.loads(f.read())
            else:
                local_path = Path(__file__).parent.parent / "data" / "ai_repos_graph.json"
                if local_path.exists():
                    with open(local_path, "rb") as f:
                        self._graph_data = orjson.loads(f.read())

        nodes = self._graph_data.get("nodes", [])
        if category:
            nodes = [n for n in nodes if n.get("category", "").lower() == category.lower()]
        if query:
            q = query.lower()
            nodes = [n for n in nodes if q in n.get("name", "").lower() or q in n.get("description", "").lower()]

        results = sorted(nodes, key=lambda x: x.get("stars", 0), reverse=True)[:10]
        output = "\n".join([
            f"{r.get('stars',0):>7}⭐ {r.get('name','')} | {r.get('category','')} | {r.get('era','')}"
            for r in results
        ])
        result = ToolResult(
            True, output,
            metadata={"count": len(results), "total": len(self._graph_data.get("nodes", []))}
        )
        self._set_cache(cache_key, result)
        return result

    def _web_search(self, query: str, max_results: int = 5) -> ToolResult:
        """Search the web using DuckDuckGo. Params: query (str), max_results (int, default 5)."""
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return ToolResult(True, f"No results for: {query}")
            lines = []
            for r in results:
                title = r.get("title", "No title")
                href = r.get("href", "")
                body = r.get("body", "")
                lines.append(f"{title}\n{href}\n{body[:200]}...")
            return ToolResult(True, "\n\n".join(lines), metadata={"count": len(results)})
        except Exception as e:
            return ToolResult(False, "", f"Web search error: {e}")

    def _performance_metrics(self) -> ToolResult:
        """Get real-time CPU, RAM, disk metrics. No params."""
        import psutil
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        disk = psutil.disk_usage("/")
        load = subprocess.run("cat /proc/loadavg", shell=True, capture_output=True, text=True).stdout.strip()
        metrics = {
            "cpu_percent": cpu,
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "ram_percent": mem.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "load_avg": load,
            "timestamp": time.time()
        }
        return ToolResult(True, json.dumps(metrics, indent=2))

    def _python_exec(self, code: str, timeout: int = 10) -> ToolResult:
        """Execute Python code in a restricted subprocess. Params: code (str), timeout (int, default 10)."""
        blocked = ["import os", "import sys", "__import__", "open(", "exec(", "eval(", "compile(", "subprocess", "os.system", "os.remove", "shutil", "ctypes", "socket", "urllib.request"]
        for b in blocked:
            if b in code:
                return ToolResult(False, "", f"Blocked pattern: '{b}'")

        wrapper = '''
import json, math, random, datetime, itertools, collections, statistics, re, string, hashlib, typing, fractions, decimal, inspect
_safe_locals = {"json": json, "math": math, "random": random, "datetime": datetime,
    "itertools": itertools, "collections": collections, "statistics": statistics,
    "re": re, "string": string, "hashlib": hashlib, "typing": typing,
    "fractions": fractions, "decimal": decimal, "inspect": inspect}
_code_result = None
_code_error = None
_output = None
try:
    _code_result = eval(compile("""''' + code.replace('"""', '\"\"\"') + '''""", "<sandbox>", "exec"), {"__builtins__": {}}, _safe_locals)
except Exception as e:
    _code_error = str(e)
if _code_result is not None:
    _output = _code_result
elif "_" in _safe_locals and _safe_locals["_"] is not None:
    _output = _safe_locals["_"]
print(json.dumps({"result": repr(_output) if _output is not None else None, "error": _code_error}))
'''
        try:
            r = subprocess.run(["python3", "-c", wrapper], capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return ToolResult(True, r.stdout, r.stderr)
            return ToolResult(False, r.stdout, r.stderr)
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _git_ops(self, command: str = "status", repo_path: str = ".") -> ToolResult:
        """Run safe git operations. Params: command (str, default 'status'), repo_path (str, default '.')."""
        allowed = ["status", "log", "diff", "branch", "show", "remote", "fetch", "pull", "stash", "config"]
        cmd_first = command.split()[0] if command.split() else ""
        if cmd_first not in allowed:
            return ToolResult(False, "", f"Git command '{cmd_first}' not allowed. Allowed: {allowed}")
        try:
            r = subprocess.run(f"cd {repo_path} && git {command}", shell=True, capture_output=True, text=True, timeout=30)
            return ToolResult(r.returncode == 0, r.stdout, r.stderr)
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _docker_ops(self, command: str = "ps") -> ToolResult:
        """Run safe docker operations. Params: command (str, default 'ps')."""
        allowed = ["ps", "images", "logs", "inspect", "stats", "top", "version", "info"]
        cmd_first = command.split()[0] if command.split() else ""
        if cmd_first not in allowed:
            return ToolResult(False, "", f"Docker command '{cmd_first}' not allowed. Allowed: {allowed}")
        try:
            r = subprocess.run(f"docker {command}", shell=True, capture_output=True, text=True, timeout=30)
            return ToolResult(r.returncode == 0, r.stdout, r.stderr)
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _repo_recommend(self, category: str = "", era: str = "", min_stars: int = 0, limit: int = 5) -> ToolResult:
        """Recommend AI repos from the research graph. Params: category (str), era (str), min_stars (int), limit (int, default 5)."""
        try:
            graph_path = self.config.get("repo_knowledge", {}).get("graph_data_path", "")
            if not graph_path or not Path(graph_path).exists():
                graph_path = Path(__file__).parent.parent / "data" / "ai_repos_graph.json"
            with open(graph_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            nodes = data.get("nodes", [])
            filtered = nodes
            if category:
                filtered = [n for n in filtered if n.get("category", "").lower() == category.lower()]
            if era:
                filtered = [n for n in filtered if n.get("era", "").lower() == era.lower()]
            if min_stars:
                filtered = [n for n in filtered if n.get("stars", 0) >= min_stars]
            filtered = sorted(filtered, key=lambda x: x.get("stars", 0), reverse=True)
            if not filtered:
                return ToolResult(True, "No repos match the criteria.")
            picks = filtered[:limit]
            lines = []
            for n in picks:
                lines.append(f"{n.get('stars', 0):>7}⭐ {n.get('name', '')} | {n.get('category', '')} | {n.get('era', '')}")
                lines.append(f"         {n.get('description', '')[:120]}")
                lines.append(f"         {n.get('url', '')}")
            return ToolResult(True, "\n".join(lines), metadata={"count": len(picks), "total": len(filtered)})
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _sql_analysis(self, sql: str, dialect: str = "sqlite") -> ToolResult:
        """Parse and analyze SQL without executing it. Params: sql (str), dialect (str, default 'sqlite')."""
        sql = sql.strip()
        if not sql:
            return ToolResult(False, "", "No SQL provided")

        info = {
            "dialect": dialect,
            "statements": [],
            "tables": set(),
            "columns": set(),
            "issues": [],
            "estimated_complexity": "low"
        }

        try:
            import sqlparse
            statements = sqlparse.parse(sql)
        except Exception:
            # Fallback: split by semicolons
            statements = [s.strip() for s in sql.split(";") if s.strip()]

        for stmt in statements:
            stmt_text = str(stmt) if hasattr(stmt, "__str__") else stmt
            info["statements"].append(stmt_text)
            stmt_upper = stmt_text.upper()

            # Detect tables with regex
            from_tables = re.findall(r'\bFROM\s+(\S+)', stmt_text, re.IGNORECASE)
            join_tables = re.findall(r'\bJOIN\s+(\S+)', stmt_text, re.IGNORECASE)
            into_tables = re.findall(r'\bINTO\s+(\S+)', stmt_text, re.IGNORECASE)
            update_tables = re.findall(r'\bUPDATE\s+(\S+)', stmt_text, re.IGNORECASE)
            create_tables = re.findall(r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)', stmt_text, re.IGNORECASE)
            tables = from_tables + join_tables + into_tables + update_tables + create_tables
            for t in tables:
                # strip trailing punctuation
                t = t.strip(",;()")
                if t:
                    info["tables"].add(t)

            # Detect columns
            select_cols = re.findall(r'\bSELECT\s+(.+?)\bFROM\b', stmt_text, re.IGNORECASE | re.DOTALL)
            for group in select_cols:
                for col in group.split(","):
                    col = col.strip().split()[0].strip(",;()")
                    if col and col != "*":
                        info["columns"].add(col)

            # Detect issues
            if "DELETE" in stmt_upper and "WHERE" not in stmt_upper:
                info["issues"].append("DELETE without WHERE clause")
            if "UPDATE" in stmt_upper and "WHERE" not in stmt_upper:
                info["issues"].append("UPDATE without WHERE clause")
            if "SELECT *" in stmt_upper:
                info["issues"].append("SELECT * used — prefer explicit columns")
            if "DROP TABLE" in stmt_upper:
                info["issues"].append("DROP TABLE detected — dangerous operation")

        # Complexity estimate
        num_joins = sum(1 for s in info["statements"] if re.search(r'\bJOIN\b', s, re.IGNORECASE))
        num_subqueries = sum(1 for s in info["statements"] if "SELECT" in s.upper())
        has_cte = any("WITH " in s.upper() for s in info["statements"])
        if num_joins > 3 or num_subqueries > 2 or has_cte:
            info["estimated_complexity"] = "high"
        elif num_joins > 0 or num_subqueries > 1:
            info["estimated_complexity"] = "medium"

        # Convert sets to lists for JSON
        info["tables"] = sorted(info["tables"])
        info["columns"] = sorted(info["columns"])
        return ToolResult(True, json.dumps(info, indent=2), metadata={"dialect": dialect, "statements_count": len(info["statements"])})

    async def _http_request(self, url: str, method: str = "GET", headers: dict = None, body: str = "", timeout: int = 15) -> ToolResult:
        """Make HTTP requests. Params: url (str), method (str, default 'GET'), headers (dict), body (str), timeout (int, default 15)."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url,
                    headers=headers or {},
                    data=body or None,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    text = await resp.text()
                    return ToolResult(
                        True, text[:5000],
                        metadata={"status": resp.status, "content_type": resp.content_type}
                    )
        except Exception as e:
            return ToolResult(False, "", str(e))

    async def _browser(self, action: str = "navigate", **kwargs) -> ToolResult:
        """Browser automation via Playwright. Params: action (str, default 'navigate'), plus action-specific kwargs."""
        try:
            from tools.browser import BrowserTool
            bt = BrowserTool()
            result = await bt(action=action, **kwargs)
            return result
        except Exception as e:
            return ToolResult(False, "", f"Browser error: {e}")

    async def _crm_query(self, action: str = "hot_leads", limit: int = 10, **kwargs) -> ToolResult:
        """Query SAHIIXX-OS CRM. Params: action (str: hot_leads, pipeline, summary, trigger_pipeline), limit (int)."""
        import aiohttp
        base = "http://localhost:1300"
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if action == "hot_leads":
                    async with session.get(f"{base}/crm/hot") as resp:
                        data = await resp.json()
                        leads = data.get("hot_leads", [])[:limit]
                        return ToolResult(True, json.dumps(leads, indent=2), metadata={"count": len(leads)})
                elif action == "pipeline":
                    async with session.get(f"{base}/pipeline") as resp:
                        data = await resp.json()
                        return ToolResult(True, json.dumps(data, indent=2))
                elif action == "summary":
                    async with session.get(f"{base}/crm/summary") as resp:
                        data = await resp.json()
                        return ToolResult(True, json.dumps(data, indent=2))
                elif action == "trigger_pipeline":
                    async with session.post(f"{base}/run/pipeline") as resp:
                        data = await resp.json()
                        return ToolResult(True, json.dumps(data, indent=2))
                else:
                    return ToolResult(False, "", f"Unknown CRM action: {action}")
        except Exception as e:
            return ToolResult(False, "", f"CRM query error: {e}")
