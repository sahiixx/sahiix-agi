"""Unified Router — dispatches tasks to the best subsystem in the SAHIIXX ecosystem."""
import re
from typing import Optional, Tuple

from core.ecosystem import EcosystemDiscovery, EcosystemBridge


class UnifiedRouter:
    """Intent-based router that maps user queries to the optimal ecosystem node."""

    def __init__(self, discovery: EcosystemDiscovery, bridge: EcosystemBridge):
        self.discovery = discovery
        self.bridge = bridge

    def classify(self, text: str) -> Tuple[str, float, str]:
        """Classify intent and return (node_name, confidence, reason)."""
        text_lower = text.lower()

        # 1. sahiixx-os: CRM, leads, pipeline, real estate, outreach
        crm_keywords = [
            "lead", "leads", "hot lead", "pipeline", "crm", "outreach",
            "contact", "follow up", "follow-up", "buyer", "investor",
            "real estate", "property", "apartment", "villa", "penthouse",
            "dubai", "abu dhabi", "uae", "emirate", "price per sqm",
            "brief", "daily brief", "morning brief", "signal",
            "whatsapp", "sms", "email", "twilio", "sendgrid",
            "budget", "million", "aed", "usd", "deal",
        ]
        crm_score = sum(1 for k in crm_keywords if k in text_lower)

        # 2. agency-agents: complex multi-agent missions, evolution, fabrication
        agency_keywords = [
            "evolve", "fabricate", "swarm", "mission", "agency",
            "security audit", "penetration test",
            "marketing campaign", "sales funnel", "content strategy",
            "152 agents", "claude core", "verdict", "preset",
            "design a saas", "build a startup", "qualify leads",
            "mult agent", "multi-agent", "parallel agents",
        ]
        agency_score = sum(1 for k in agency_keywords if k in text_lower)

        # 3. sahiix-agi: fast chat, status, metrics, memory, tools
        meta_keywords = [
            "status", "health", "metrics", "memory", "episode",
            "tool", "shell", "system info", "performance",
            "agent list", "mission list", "explore", "evolve",
            "fabricate tool", "autonomy", "self-improve",
        ]
        meta_score = sum(1 for k in meta_keywords if k in text_lower)

        scores = {
            "sahiixx-os": crm_score,
            "agency-agents": agency_score,
            "sahiix-agi": meta_score,
        }

        best = max(scores, key=scores.get)
        best_score = scores[best]

        # Check availability
        available = {n.name for n in self.discovery.get_available()}
        if best not in available and best != "sahiix-agi":
            if "sahiix-agi" in available:
                return "sahiix-agi", 0.5, f"Preferred '{best}' unavailable, falling back"
            for alt in ["sahiixx-os", "agency-agents", "sahiix-agi"]:
                if alt in available:
                    return alt, 0.4, f"Fallback to {alt}"

        if best_score == 0:
            return "sahiix-agi", 0.3, "No strong intent match, defaulting to meta-orchestrator"

        return best, min(0.95, 0.4 + best_score * 0.15), f"Matched {best} with score {best_score}"

    async def route(self, text: str, context: dict = None) -> dict:
        """Route a query and return result + metadata."""
        node, confidence, reason = self.classify(text)

        if node == "sahiix-agi":
            return {
                "node": node,
                "confidence": confidence,
                "reason": reason,
                "result": None,
                "dispatched": False,
            }

        result_text = await self.bridge.dispatch_chat(node, text, context)
        return {
            "node": node,
            "confidence": confidence,
            "reason": reason,
            "result": result_text,
            "dispatched": True,
        }
