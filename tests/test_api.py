"""Integration tests for FastAPI endpoints."""
import pytest
import pytest_asyncio
import asyncio
import httpx
from fastapi import FastAPI

# We test main.py indirectly by importing the app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest_asyncio.fixture
async def client():
    import main
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as ac:
        yield ac
    # Cleanup: httpx 0.28.1 ASGITransport does not support lifespan events,
    # so the FastAPI lifespan context manager never runs. We must manually
    # close the lazy-initialized director to prevent aiosqlite background
    # threads from keeping the pytest process alive.
    if main.director is not None:
        try:
            await main.director.close()
        except Exception:
            pass
        main.director = None


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_agents_endpoint(client):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert isinstance(data["agents"], list)


@pytest.mark.asyncio
async def test_tools_endpoint(client):
    response = await client.get("/api/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)


@pytest.mark.asyncio
async def test_status_endpoint(client):
    response = await client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "memory_episodes" in data
    # Verify no deadlock occurred (should return in < 5s since httpx has timeouts)
