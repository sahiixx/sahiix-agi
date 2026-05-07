"""High-performance async memory store with WAL mode, connection pooling, and Qdrant vector backend."""
import asyncio
import json
import hashlib
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

import aiosqlite

try:
    from core.vector_db import QdrantVectorDB
except Exception:
    QdrantVectorDB = None

_DEFAULT_QDRANT_DIM = 768

# Module-level process-global init guard (each uvicorn worker is a separate process)
_INIT_LOCK = asyncio.Lock()
_INIT_DONE = False


class MemoryStore:
    def __init__(self, db_path: str, pool_size: int = 5, qdrant_url: Optional[str] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pool_size = pool_size
        self.qdrant_url = qdrant_url
        self._pool: List[aiosqlite.Connection] = []
        self._pool_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._in_memory_cache: Dict[str, Any] = {}
        self._cache_ttl = 30
        self._init_done = False
        self._qdrant: Optional[QdrantVectorDB] = None
        self._qdrant_dim: int = _DEFAULT_QDRANT_DIM

    async def _ensure_qdrant(self):
        if self._qdrant is None and QdrantVectorDB is not None and self.qdrant_url:
            try:
                q = QdrantVectorDB(self.qdrant_url)
                await q.connect()
                await q.create_collection("episodes", dim=self._qdrant_dim)
                self._qdrant = q
            except Exception:
                self._qdrant = None

    async def _get_conn(self) -> aiosqlite.Connection:
        async with self._pool_lock:
            if self._pool:
                return self._pool.pop()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute("PRAGMA cache_size=-64000")
        await conn.execute("PRAGMA temp_store=MEMORY")
        await conn.execute("PRAGMA mmap_size=268435456")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def _return_conn(self, conn: aiosqlite.Connection):
        if len(self._pool) < self.pool_size:
            self._pool.append(conn)
            return
        await conn.close()

    async def init(self):
        global _INIT_LOCK
        async with _INIT_LOCK:
            if getattr(self, '_init_done', False):
                return
            self._init_done = True
        print('[MemoryStore.init] starting')
        conn = await self._get_conn()
        print('[MemoryStore.init] got conn')
        stmts = [
            "CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, role TEXT, content TEXT, agent TEXT, tags TEXT, hash TEXT UNIQUE)",
            "CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, key TEXT UNIQUE, value TEXT, source TEXT)",
            "CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, title TEXT, messages TEXT)",
            "CREATE TABLE IF NOT EXISTS embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER UNIQUE, vector TEXT, model TEXT, timestamp REAL, FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_agent ON episodes(agent)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_tags ON episodes(tags)",
            "CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_time ON episodes(timestamp DESC)",
            "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model)",
        ]
        for sql in stmts:
            await conn.execute(sql)
        await conn.commit()
        await self._return_conn(conn)
        await self._ensure_qdrant()

    def _hash(self, *parts) -> str:
        return hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=16).hexdigest()

    async def save_episode(self, role: str, content: str, agent: str = "system", tags: str = "") -> int:
        await self.init()
        h = self._hash(role, content, agent)
        async with self._write_lock:
            conn = await self._get_conn()
            try:
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO episodes (timestamp, role, content, agent, tags, hash) VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), role, content, agent, tags, h)
                )
                await conn.commit()
                row = await conn.execute_fetchall("SELECT id FROM episodes WHERE hash = ?", (h,))
                return row[0][0] if row else cur.lastrowid or -1
            finally:
                await self._return_conn(conn)

    async def get_recent(self, agent: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        cache_key = f"recent:{agent}:{limit}"
        cached = self._in_memory_cache.get(cache_key)
        if cached and (time.time() - cached.get("_cache_time", 0)) < self._cache_ttl:
            return cached["data"]

        await self.init()
        conn = await self._get_conn()
        try:
            conn.row_factory = aiosqlite.Row
            if agent:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM episodes WHERE agent = ? ORDER BY timestamp DESC LIMIT ?",
                    (agent, limit)
                )
            else:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?", (limit,)
                )
            result = [dict(r) for r in rows]
            self._in_memory_cache[cache_key] = {"data": result, "_cache_time": time.time()}
            return result
        finally:
            await self._return_conn(conn)

    async def get_conversation_thread(self, agent: Optional[str] = None, limit: int = 10) -> List[Dict[str, str]]:
        """Return recent episodes formatted as role/content for LLM context."""
        eps = await self.get_recent(agent=agent, limit=limit)
        return [{"role": ep["role"], "content": ep["content"]} for ep in reversed(eps)]

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        keywords = [k.lower() for k in query.split() if len(k) > 2]
        if not keywords:
            return []

        await self.init()
        conn = await self._get_conn()
        try:
            conn.row_factory = aiosqlite.Row
            placeholders = " OR ".join(["LOWER(content) LIKE ?" for _ in keywords])
            params = [f"%{k}%" for k in keywords] + [limit]
            rows = await conn.execute_fetchall(
                f"SELECT * FROM episodes WHERE {placeholders} ORDER BY timestamp DESC LIMIT ?",
                params
            )
            return [dict(r) for r in rows]
        finally:
            await self._return_conn(conn)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    async def save_embedding(self, episode_id: int, vector: list[float], model: str = ""):
        await self.init()
        async with self._write_lock:
            conn = await self._get_conn()
            try:
                await conn.execute(
                    "INSERT OR REPLACE INTO embeddings (episode_id, vector, model, timestamp) VALUES (?, ?, ?, ?)",
                    (episode_id, json.dumps(vector), model, time.time())
                )
                await conn.commit()
            finally:
                await self._return_conn(conn)
        # Sync to Qdrant if available
        if self._qdrant is None and self.qdrant_url:
            await self._ensure_qdrant()
        if self._qdrant is not None:
            try:
                await self._qdrant.upsert("episodes", [{
                    "id": episode_id,
                    "vector": vector,
                    "payload": {"model": model, "episode_id": episode_id}
                }])
            except Exception:
                pass

    async def vector_search(self, query_vector: list[float], limit: int = 10, agent: Optional[str] = None, min_score: float = 0.0) -> List[Dict[str, Any]]:
        if not query_vector:
            return []
        await self.init()
        # Try Qdrant first
        if self._qdrant is None and self.qdrant_url:
            await self._ensure_qdrant()
        if self._qdrant is not None:
            try:
                results = await self._qdrant.search("episodes", query_vector, limit=limit)
                out = []
                for r in results:
                    if r.get("score", 0) < min_score:
                        continue
                    ep_rows = None
                    conn = await self._get_conn()
                    try:
                        conn.row_factory = aiosqlite.Row
                        ep_rows = await conn.execute_fetchall(
                            "SELECT * FROM episodes WHERE id = ? LIMIT 1", (r["id"],)
                        )
                    finally:
                        await self._return_conn(conn)
                    if ep_rows:
                        item = dict(ep_rows[0])
                        item["_score"] = round(r["score"], 4)
                        out.append(item)
                return out
            except Exception:
                pass
        # Fallback: local cosine similarity via SQLite
        conn = await self._get_conn()
        try:
            conn.row_factory = aiosqlite.Row
            if agent:
                rows = await conn.execute_fetchall(
                    """SELECT e.*, em.vector FROM episodes e
                       JOIN embeddings em ON e.id = em.episode_id
                       WHERE e.agent = ?""",
                    (agent,)
                )
            else:
                rows = await conn.execute_fetchall(
                    """SELECT e.*, em.vector FROM episodes e
                       JOIN embeddings em ON e.id = em.episode_id"""
                )
            scored = []
            qv = query_vector
            for r in rows:
                vec = json.loads(r["vector"])
                if len(vec) != len(qv):
                    continue
                score = self._cosine_similarity(qv, vec)
                if score >= min_score:
                    item = dict(r)
                    item["_score"] = round(score, 4)
                    scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item[1] for item in scored[:limit]]
        finally:
            await self._return_conn(conn)

    async def save_fact(self, key: str, value: str, source: str = ""):
        await self.init()
        async with self._write_lock:
            conn = await self._get_conn()
            try:
                await conn.execute(
                    "INSERT OR REPLACE INTO facts (timestamp, key, value, source) VALUES (?, ?, ?, ?)",
                    (time.time(), key, value, source)
                )
                await conn.commit()
            finally:
                await self._return_conn(conn)

    async def get_fact(self, key: str) -> Optional[str]:
        cache_key = f"fact:{key}"
        cached = self._in_memory_cache.get(cache_key)
        if cached and (time.time() - cached.get("_cache_time", 0)) < self._cache_ttl:
            return cached["data"]

        await self.init()
        conn = await self._get_conn()
        try:
            row = await conn.execute_fetchall("SELECT value FROM facts WHERE key = ?", (key,))
            result = row[0][0] if row else None
            self._in_memory_cache[cache_key] = {"data": result, "_cache_time": time.time()}
            return result
        finally:
            await self._return_conn(conn)

    async def save_conversation(self, title: str, messages: List[Dict[str, str]]) -> int:
        await self.init()
        async with self._write_lock:
            conn = await self._get_conn()
            try:
                cur = await conn.execute(
                    "INSERT INTO conversations (timestamp, title, messages) VALUES (?, ?, ?)",
                    (time.time(), title, json.dumps(messages))
                )
                await conn.commit()
                return cur.lastrowid
            finally:
                await self._return_conn(conn)

    async def get_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        await self.init()
        conn = await self._get_conn()
        try:
            conn.row_factory = aiosqlite.Row
            rows = await conn.execute_fetchall(
                "SELECT * FROM conversations ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in rows]
        finally:
            await self._return_conn(conn)

    async def close(self):
        if self._qdrant is not None:
            try:
                await self._qdrant.disconnect()
            except Exception:
                pass
            self._qdrant = None
        async with self._pool_lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
