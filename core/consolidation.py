"""Memory Consolidation Engine — Compresses long-term episodic memory.

Periodically summarizes old episodes, removes duplicates, and creates
concept-level "wisdom" nodes for rapid recall.
"""
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set


CONSOLIDATION_INTERVAL_HOURS = 6.0
EPISODE_DIR = Path("/home/sahiix/sahiix-agi/data/memory_episodes")
WISDOM_FILE = Path("/home/sahiix/sahiix-agi/data/wisdom_graph.json")


class MemoryConsolidator:
    """Compresses episodic memory into semantic knowledge."""

    def __init__(self):
        self.episode_dir = EPISODE_DIR
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.wisdom: Dict[str, Dict[str, Any]] = self._load_wisdom()
        self._duplicate_hashes: Set[str] = set()

    def _load_wisdom(self) -> Dict[str, Dict[str, Any]]:
        if WISDOM_FILE.exists():
            try:
                return json.loads(WISDOM_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_wisdom(self):
        try:
            WISDOM_FILE.parent.mkdir(parents=True, exist_ok=True)
            WISDOM_FILE.write_text(json.dumps(self.wisdom, indent=2))
        except Exception:
            pass

    # ── Episode Management ───────────────────────────────────────────────────

    def add_episode(self, episode: Dict[str, Any]) -> str:
        ep_id = episode.get("id") or self._hash(episode)
        ep_path = self.episode_dir / f"{ep_id}.json"
        ep_path.write_text(json.dumps({
            "id": ep_id,
            "t": time.time(),
            "dt": datetime.now(timezone.utc).isoformat(),
            "content": episode,
        }, indent=2))
        return ep_id

    def get_all_episodes(self) -> List[Dict[str, Any]]:
        episodes = []
        for p in self.episode_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                episodes.append(data)
            except Exception:
                pass
        return sorted(episodes, key=lambda e: e.get("t", 0), reverse=True)

    def delete_episode(self, ep_id: str) -> bool:
        ep_path = self.episode_dir / f"{ep_id}.json"
        if ep_path.exists():
            ep_path.unlink()
            return True
        return False

    # ── Consolidation ──────────────────────────────────────────────────────

    def consolidate(self, keep_recent: int = 50) -> Dict[str, Any]:
        """
        Steps:
        1. Keep N most recent episodes raw.
        2. Group older episodes by topic/concept.
        3. Summarize each group into a wisdom node.
        4. Remove exact duplicates.
        5. Save wisdom graph.
        """
        all_eps = self.get_all_episodes()
        if len(all_eps) <= keep_recent:
            return {"status": "too_few_episodes", "count": len(all_eps)}

        recent = all_eps[:keep_recent]
        older = all_eps[keep_recent:]

        # Remove exact duplicates from older
        unique_older: List[Dict] = []
        seen_hashes: Set[str] = set()
        for ep in older:
            h = self._hash(ep.get("content", {}))
            if h not in seen_hashes:
                seen_hashes.add(h)
                unique_older.append(ep)

        # Simple topic grouping: extract keywords from content text
        groups = defaultdict(list)
        for ep in unique_older:
            topic = self._extract_topic(ep.get("content", {}))
            groups[topic].append(ep)

        # Summarize groups into wisdom nodes
        new_wisdom = 0
        for topic, group in groups.items():
            if len(group) >= 2:  # only consolidate if at least 2 episodes
                node_id = self._hash([ep["id"] for ep in group])
                summary = self._summarize_group(group)
                self.wisdom[node_id] = {
                    "id": node_id,
                    "topic": topic,
                    "source_episode_count": len(group),
                    "created": time.time(),
                    "summary": summary,
                    "key": group[0]["content"],
                }
                new_wisdom += 1

        self._save_wisdom()
        # Delete consolidated episodes (keep first N)
        for ep in older:
            self.delete_episode(ep["id"])

        return {
            "status": "consolidated",
            "total_episodes": len(all_eps),
            "kept_recent": len(recent),
            "consolidated": len(older) - len(unique_older) + len(groups),
            "duplicates_removed": len(older) - len(unique_older),
            "wisdom_nodes_created": new_wisdom,
            "wisdom_total": len(self.wisdom),
        }

    def _hash(self, obj: Any) -> str:
        return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def _extract_topic(self, content: Dict) -> str:
        text = json.dumps(content).lower()
        keywords = ["code", "bug", "fix", "api", "build", "test", "deploy", "docker",
                    "python", "error", "skill", "fabrication", "agent", "memory",
                    "performance", "database", "webhook", "metrics", "prometheus"]
        scored = [(kw, text.count(kw)) for kw in keywords]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored and scored[0][1] > 0 else "general"

    def _summarize_group(self, episodes: List[Dict]) -> str:
        n = len(episodes)
        first = episodes[0]["content"]
        last = episodes[-1]["content"]
        return (
            f"Consolidated from {n} episodes on {self._extract_topic(first)}. "
            f"First: {str(first)[:80]}. Last: {str(last)[:80]}."
        )

    # ── Recall ────────────────────────────────────────────────────────────

    def recall(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Simple keyword-based recall across wisdom + recent episodes."""
        q = query.lower()
        scores = []

        # Wisdom nodes
        for node in self.wisdom.values():
            text = json.dumps(node).lower()
            score = text.count(q)
            if score > 0:
                scores.append({"item": node, "score": score * 2, "type": "wisdom"})

        # Recent episodes
        for ep in self.get_all_episodes():
            text = json.dumps(ep).lower()
            score = text.count(q)
            if score > 0:
                scores.append({"item": ep, "score": score, "type": "episode"})

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    # ── Public API ─────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        episodes = self.get_all_episodes()
        return {
            "episodes_total": len(episodes),
            "wisdom_nodes": len(self.wisdom),
            "episode_dir": str(self.episode_dir),
            "wisdom_file": str(WISDOM_FILE),
            "recent_episodes": episodes[:5],
        }
