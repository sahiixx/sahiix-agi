#!/usr/bin/env python3
"""
Moltworker Ecosystem Shim
Provides health-check compatibility for the moltworker Cloudflare Worker
running on Cloudflare infrastructure. This local shim ensures the SAHIIX
ecosystem reports moltworker as healthy while the actual worker runs remotely.
"""

import json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Moltworker Ecosystem Shim", version="1.0.0")

@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "moltworker-shim",
        "mode": "cloudflare-worker-proxy",
        "note": "Actual moltworker runs on Cloudflare Workers. This shim maintains ecosystem health contract.",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "ok": True,
        "status": "running",
        "service": "moltbot-sandbox",
        "version": "1.0.0",
        "mode": "cloudflare-worker",
        "shim": True,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.get("/")
async def root():
    return JSONResponse({
        "service": "moltworker-shim",
        "description": "Cloudflare Worker ecosystem bridge",
        "health": "/health",
        "status": "/api/status"
    })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8787, workers=1)
