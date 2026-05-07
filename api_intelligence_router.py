"""FastAPI router that proxies requests to the SAHIIXX OS Intelligence API on :8082."""
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

INTELLIGENCE_BASE_URL = "http://localhost:8082"

router = APIRouter(tags=["intelligence"])

# Reusable async client with reasonable limits
_httpx_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            base_url=INTELLIGENCE_BASE_URL,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _httpx_client


async def _proxy_request(request: Request, path: str = "") -> Response:
    """Proxy an incoming request to the intelligence API preserving method, body, headers and query params."""
    client = _get_client()

    method = request.method
    url = f"/{path}" if path else "/"

    # Forward relevant headers (drop host to avoid confusion)
    headers = {}
    for key, value in request.headers.items():
        if key.lower() in ("host", "content-length"):
            continue
        headers[key] = value

    # Read body if present
    body = await request.body()

    try:
        proxy_resp = await client.request(
            method=method,
            url=url,
            params=request.query_params,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": "Intelligence API unreachable", "error": str(exc)},
        )
    except httpx.TimeoutException as exc:
        return JSONResponse(
            status_code=504,
            content={"detail": "Intelligence API timeout", "error": str(exc)},
        )

    # Stream response back for large payloads or if already streaming
    content_type = proxy_resp.headers.get("content-type", "application/json")
    if "text/event-stream" in content_type:
        return StreamingResponse(
            content=proxy_resp.aiter_raw(),
            status_code=proxy_resp.status_code,
            headers={"content-type": content_type},
        )

    return Response(
        content=proxy_resp.content,
        status_code=proxy_resp.status_code,
        headers={"content-type": content_type},
    )


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def intelligence_proxy(request: Request, path: str = ""):
    """Catch-all proxy for /api/intelligence/* to the intelligence API."""
    return await _proxy_request(request, path)


@router.get("/health")
async def intelligence_health():
    """Aggregate health check for SAHIIX-AGI + Intelligence API."""
    client = _get_client()
    upstream = {"reachable": False, "status": "unknown", "latency_ms": None}
    start = time.monotonic()
    try:
        resp = await client.get("/health", timeout=5.0)
        upstream["reachable"] = True
        upstream["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
        upstream["status"] = "healthy" if resp.status_code == 200 else "degraded"
        try:
            upstream["payload"] = resp.json()
        except Exception:
            upstream["payload"] = None
    except Exception as exc:
        upstream["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
        upstream["error"] = str(exc)

    return {
        "proxy": "ok",
        "intelligence_api": upstream,
        "timestamp": time.time(),
    }
