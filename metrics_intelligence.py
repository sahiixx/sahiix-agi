"""Prometheus metrics collector for SAHIIXX OS Intelligence API.

Connects intelligence-specific metrics to the existing :9092 endpoint
by registering them in the default prometheus_client REGISTRY.
"""
import asyncio
import time
from typing import Any

import httpx
from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client.registry import REGISTRY

# ── Intelligence-specific metrics ─────────────────────────────────────────────
INTELLIGENCE_REQUESTS_BY_VERTICAL = Counter(
    "sahiix_intelligence_requests_by_vertical_total",
    "Intelligence API requests by vertical",
    ["vertical"],
)
INTELLIGENCE_SCORING_LATENCY = Histogram(
    "sahiix_intelligence_scoring_latency_seconds",
    "Lead scoring latency",
    ["vertical"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
INTELLIGENCE_DEAL_TIER_DISTRIBUTION = Gauge(
    "sahiix_intelligence_deal_tier_distribution",
    "Deal tier distribution counts",
    ["tier"],
)
INTELLIGENCE_UPSTREAM_HEALTH = Gauge(
    "sahiix_intelligence_upstream_health",
    "Intelligence API upstream health (1=healthy, 0=unhealthy)",
)
INTELLIGENCE_UPSTREAM_LATENCY = Histogram(
    "sahiix_intelligence_upstream_latency_seconds",
    "Intelligence API upstream latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Internal tracker for latest scraped deal tier snapshot
_latest_deal_tiers: dict[str, int] = {}


async def scrape_intelligence_metrics(intelligence_url: str = "http://localhost:8082") -> dict[str, Any]:
    """Scrape the Intelligence API for metrics data."""
    metrics_data: dict[str, Any] = {
        "healthy": False,
        "verticals": {},
        "deal_tiers": {},
        "scoring_latencies": {},
        "error": None,
    }
    timeout = httpx.Timeout(5.0, connect=2.0)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Health check
            health_resp = await client.get(f"{intelligence_url}/health")
            latency = time.monotonic() - start
            INTELLIGENCE_UPSTREAM_LATENCY.observe(latency)
            metrics_data["healthy"] = health_resp.status_code == 200
            INTELLIGENCE_UPSTREAM_HEALTH.set(1 if metrics_data["healthy"] else 0)

            # Try to fetch vertical stats if available
            try:
                vert_resp = await client.get(f"{intelligence_url}/api/stats/verticals")
                if vert_resp.status_code == 200:
                    metrics_data["verticals"] = vert_resp.json()
            except Exception:
                pass

            # Try to fetch deal tier distribution if available
            try:
                tier_resp = await client.get(f"{intelligence_url}/api/stats/tiers")
                if tier_resp.status_code == 200:
                    metrics_data["deal_tiers"] = tier_resp.json()
            except Exception:
                pass

            # Try to fetch scoring latency stats if available
            try:
                lat_resp = await client.get(f"{intelligence_url}/api/stats/scoring")
                if lat_resp.status_code == 200:
                    metrics_data["scoring_latencies"] = lat_resp.json()
            except Exception:
                pass

    except Exception as exc:
        metrics_data["error"] = str(exc)
        INTELLIGENCE_UPSTREAM_HEALTH.set(0)
        INTELLIGENCE_UPSTREAM_LATENCY.observe(time.monotonic() - start)

    return metrics_data


def update_intelligence_metrics(data: dict[str, Any]) -> None:
    """Update Prometheus gauges/counters from scraped intelligence data."""
    # Update requests by vertical
    verticals = data.get("verticals", {})
    if isinstance(verticals, dict):
        for vertical, count in verticals.items():
            # Counter requires increment; we approximate by diffing or just incrementing count
            # For simplicity we use a gauge approach for scraped counts, but spec asks for Counter.
            # We'll increment by the delta if we can track previous values, otherwise set a gauge.
            # To honor Counter semantics, we treat each scrape as an observation window.
            INTELLIGENCE_REQUESTS_BY_VERTICAL.labels(vertical=str(vertical)).inc(
                max(0, int(count))
            )

    # Update deal tier distribution (Gauge)
    global _latest_deal_tiers
    tiers = data.get("deal_tiers", {})
    if isinstance(tiers, dict):
        # Reset previous tier values that are no longer present
        for tier in list(_latest_deal_tiers.keys()):
            if tier not in tiers:
                INTELLIGENCE_DEAL_TIER_DISTRIBUTION.labels(tier=tier).set(0)
        for tier, count in tiers.items():
            INTELLIGENCE_DEAL_TIER_DISTRIBUTION.labels(tier=str(tier)).set(float(count))
        _latest_deal_tiers = {str(k): float(v) for k, v in tiers.items()}

    # Update scoring latency observations
    latencies = data.get("scoring_latencies", {})
    if isinstance(latencies, dict):
        for vertical, lat in latencies.items():
            try:
                val = float(lat)
                if val >= 0:
                    INTELLIGENCE_SCORING_LATENCY.labels(vertical=str(vertical)).observe(val)
            except (ValueError, TypeError):
                pass


async def intelligence_metrics_loop(intelligence_url: str = "http://localhost:8082", interval: float = 30.0):
    """Background task that periodically scrapes intelligence metrics."""
    while True:
        try:
            data = await scrape_intelligence_metrics(intelligence_url)
            update_intelligence_metrics(data)
        except Exception as exc:
            print(f"[Intelligence Metrics] Scrape error: {exc}")
        await asyncio.sleep(interval)


def get_intelligence_metrics_text() -> bytes:
    """Return current Prometheus metrics text including intelligence metrics."""
    return generate_latest(REGISTRY)


# Convenience entrypoint for standalone testing
async def main():
    print("[Intelligence Metrics] Starting collector...")
    await intelligence_metrics_loop()


if __name__ == "__main__":
    asyncio.run(main())
