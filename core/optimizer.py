"""Performance Optimizer — Tracks latency, token cost, and model selection.

Auto-selects the fastest/cheapest model for a task tier and optimizes
prompt lengths to stay within budget.
"""
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional


OPTIMIZER_LOG = Path("/home/sahiix/sahiix-agi/data/optimizer_log.json")
MODEL_REGISTRY = {
    "deepseek-v4-flash:cloud": {"latency_ms": 250, "cost_per_1k": 0.0, "quality": 7},
    "kimi-k2.6:cloud":         {"latency_ms": 3500, "cost_per_1k": 0.0, "quality": 9},
    "qwen2.5:7b":              {"latency_ms": 4500, "cost_per_1k": 0.0, "quality": 8},
}
TIER_THRESHOLDS = {
    "critical": 0.95,  # use highest quality
    "complex": 0.80,
    "standard": 0.60,
    "simple": 0.40,
}


@dataclass
class InferenceRecord:
    model: str
    latency_ms: float
    tokens_in: int
    tokens_out: int
    success: bool
    timestamp: float
    task_tier: str = "standard"


class PerformanceOptimizer:
    """Tracks model performance and auto-selects optimal model per task tier."""

    def __init__(self):
        self._records: List[InferenceRecord] = self._load_log()
        self._model_stats: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {"latency": [], "success": []})
        self._rebuild_stats()

    def _load_log(self) -> List[InferenceRecord]:
        if OPTIMIZER_LOG.exists():
            try:
                raw = json.loads(OPTIMIZER_LOG.read_text())
                return [InferenceRecord(**r) for r in raw]
            except Exception:
                pass
        return []

    def _save_log(self):
        try:
            OPTIMIZER_LOG.parent.mkdir(parents=True, exist_ok=True)
            OPTIMIZER_LOG.write_text(json.dumps([
                {k: getattr(r, k) for k in r.__dataclass_fields__} for r in self._records[-500:]
            ], indent=2, default=str))
        except Exception:
            pass

    def _rebuild_stats(self):
        self._model_stats.clear()
        for r in self._records:
            self._model_stats[r.model]["latency"].append(r.latency_ms)
            self._model_stats[r.model]["success"].append(1.0 if r.success else 0.0)

    # ── Recording ───────────────────────────────────────────────────────────

    def record_inference(self, model: str, latency_ms: float, tokens_in: int, tokens_out: int,
                         success: bool = True, task_tier: str = "standard"):
        rec = InferenceRecord(
            model=model,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            timestamp=time.time(),
            task_tier=task_tier,
        )
        self._records.append(rec)
        self._model_stats[model]["latency"].append(latency_ms)
        self._model_stats[model]["success"].append(1.0 if success else 0.0)
        self._save_log()

    # ── Model Selection ───────────────────────────────────────────────────

    def select_model(self, task_tier: str = "standard", prefer_speed: bool = False) -> Optional[str]:
        """Pick the best model for a task tier."""
        threshold = TIER_THRESHOLDS.get(task_tier, 0.60)
        candidates = []

        for model, spec in MODEL_REGISTRY.items():
            stats = self._model_stats.get(model, {"latency": [], "success": []})
            avg_latency = sum(stats["latency"][-20:]) / min(len(stats["latency"]), 20) if stats["latency"] else spec["latency_ms"]
            success_rate = sum(stats["success"][-20:]) / min(len(stats["success"]), 20) if stats["success"] else 1.0
            quality = spec["quality"] * success_rate

            if quality < threshold * 10:
                continue

            score = (1 / (avg_latency + 1)) if prefer_speed else (quality / (avg_latency + 1))
            candidates.append((model, score, avg_latency, quality))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    # ── Prompt Optimization ────────────────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        """Rough approximation: 1 token ≈ 4 characters for english."""
        return max(1, len(text) // 4)

    def optimize_prompt(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> Dict[str, Any]:
        """If prompt is too long, compress it while keeping key instructions."""
        total = self.estimate_tokens(system_prompt) + self.estimate_tokens(user_prompt)
        if total <= max_tokens:
            return {"system": system_prompt, "user": user_prompt, "tokens": total, "compressed": False}

        # Truncate user prompt from the bottom (oldest context) first
        compressed_user = user_prompt
        while self.estimate_tokens(compressed_user) + self.estimate_tokens(system_prompt) > max_tokens and len(compressed_user) > 200:
            # chop ~20%
            compressed_user = compressed_user[:int(len(compressed_user) * 0.8)]

        return {
            "system": system_prompt,
            "user": compressed_user,
            "tokens": self.estimate_tokens(system_prompt) + self.estimate_tokens(compressed_user),
            "compressed": True,
            "original_tokens": total,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "records_total": len(self._records),
            "model_stats": {
                model: {
                    "avg_latency_ms": round(sum(v["latency"][-20:]) / min(len(v["latency"]), 20), 1) if v["latency"] else None,
                    "success_rate": round(sum(v["success"][-20:]) / min(len(v["success"]), 20), 2) if v["success"] else None,
                    "inference_count": len(v["latency"]),
                }
                for model, v in self._model_stats.items()
            },
            "tier_thresholds": TIER_THRESHOLDS,
        }

    def get_recommendation(self, task_description: str) -> Dict[str, Any]:
        tier = "standard"
        t = task_description.lower()
        if "critical" in t or "production" in t:
            tier = "critical"
        elif "complex" in t or "architecture" in t:
            tier = "complex"
        elif "simple" in t or "quick" in t:
            tier = "simple"

        model = self.select_model(tier, prefer_speed="fast" in t)
        return {
            "task_tier": tier,
            "recommended_model": model,
            "reason": f"Selected for {tier} tier based on success rate and latency.",
        }
