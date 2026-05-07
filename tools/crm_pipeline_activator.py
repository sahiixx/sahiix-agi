#!/usr/bin/env python3
"""CRM Pipeline Activator — moves frozen 'new' leads into active outreach pipeline."""
import asyncio
import time
from typing import Dict, List, Any, Optional

import aiohttp

CRM_BASE = "http://127.0.0.1:1300"
BATCH_SIZE = 10
DELAY_BETWEEN_BATCHES = 1.0  # seconds


def classify_lead(lead: Dict[str, Any]) -> str:
    """Determine outreach strategy by lead quality."""
    score = lead.get("score", 0)
    budget = lead.get("budget", 0)
    source = lead.get("source", "")
    if score >= 92 and budget > 200_000_000:
        return "vip_direct"
    if score >= 92:
        return "high_touch"
    if score >= 80:
        return "standard"
    return "nurture"


def enrich_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Build pipeline activation payload from lead record."""
    tier = classify_lead(lead)
    return {
        "lead_id": lead.get("id"),
        "name": lead.get("name"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "budget": lead.get("budget"),
        "area": lead.get("area"),
        "score": lead.get("score"),
        "source": lead.get("source"),
        "pipeline_state": "active_outreach",
        "status": "qualified",
        "outreach_tier": tier,
        "outreach_priority": {"vip_direct": "urgent", "high_touch": "high", "standard": "medium", "nurture": "low"}.get(tier, "medium"),
        "first_contact_method": "voice" if lead.get("phone") and tier in ("vip_direct", "high_touch") else "email",
        "estimated_value_usd": round(lead.get("budget", 0) / 3.67, 2) if lead.get("budget") else None,
        "pipeline_entry_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


async def activate_lead(session: aiohttp.ClientSession, lead: Dict[str, Any]) -> Dict[str, Any]:
    """Push a single lead into active outreach via CRM /leads/buyer-signals/batch (fallback to /lead/{id})"""
    payload = enrich_payload(lead)
    lead_id = lead.get("id")

    # Try batch endpoint first
    try:
        async with session.post(
            f"{CRM_BASE}/api/v1/leads/buyer-signals/batch",
            json=[payload],
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return {"lead_id": lead_id, "status": "activated", "tier": payload["outreach_tier"], "method": "batch"}
    except Exception:
        pass

    # Fallback: update single lead
    try:
        async with session.patch(
            f"{CRM_BASE}/api/v1/lead/{lead_id}",
            json={"pipeline_state": "active_outreach", "status": "qualified", "outreach_tier": payload["outreach_tier"]},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 201, 204):
                return {"lead_id": lead_id, "status": "activated", "tier": payload["outreach_tier"], "method": "patch"}
            else:
                return {"lead_id": lead_id, "status": f"error_{resp.status}", "tier": payload["outreach_tier"], "method": "patch"}
    except Exception as exc:
        return {"lead_id": lead_id, "status": f"failed_{type(exc).__name__}", "tier": payload["outreach_tier"], "method": "patch"}


async def run_activation(leads: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, Any]:
    """Batch-process leads with backpressure."""
    if not leads:
        return {"total": 0, "activated": 0, "failures": 0, "results": []}

    if dry_run:
        tiers = {}
        for l in leads:
            t = classify_lead(l)
            tiers[t] = tiers.get(t, 0) + 1
        return {"mode": "dry_run", "total": len(leads), "tier_breakdown": tiers, "would_activate": len(leads)}

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        results = []
        activated = 0
        failures = 0

        for idx in range(0, len(leads), BATCH_SIZE):
            batch = leads[idx: idx + BATCH_SIZE]
            tasks = [activate_lead(session, l) for l in batch]
            batch_results = await asyncio.gather(*tasks)

            for r in batch_results:
                results.append(r)
                if r["status"] == "activated":
                    activated += 1
                else:
                    failures += 1

            if idx + BATCH_SIZE < len(leads):
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        return {
            "total": len(leads),
            "activated": activated,
            "failures": failures,
            "results": results[:10],
        }


if __name__ == "__main__":
    import sys, json
    dry = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        with open(args[0]) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    leads = data.get("leads", data)
    report = asyncio.run(run_activation(leads, dry_run=dry))
    print(json.dumps(report, indent=2))
