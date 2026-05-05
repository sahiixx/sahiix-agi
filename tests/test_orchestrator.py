"""Tests for orchestrator layer."""
import pytest
import pytest_asyncio
import asyncio
import tempfile
import os
from core.orchestrator import Director


@pytest_asyncio.fixture
async def director():
    config_content = """
system:
  name: test
llm:
  default_provider: ollama
  providers:
    ollama:
      base_url: http://localhost:11434
      model: kimi-k2.6:cloud
      timeout: 5
  fallback_chain: [ollama]
memory:
  type: sqlite
  path: /tmp/test_agi.db
agents:
  - name: director
    description: test
    system_prompt: You are a test director.
    priority: 0
  - name: coder
    description: test coder
    system_prompt: You are a test coder.
    priority: 1
tools:
  enabled: [shell]
  shell:
    allowed_commands: [ls]
    blocked_patterns: []
"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.write(fd, config_content.encode())
    os.close(fd)
    d = Director(path)
    await d.warmup()
    yield d
    await d.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_director_init(director):
    assert "director" in director.agents
    assert "coder" in director.agents


@pytest.mark.asyncio
async def test_detect_specialist_code(director):
    assert director._detect_specialist("write a python function") == "coder"


@pytest.mark.asyncio
async def test_detect_specialist_research(director):
    assert director._detect_specialist("research the latest AI trends") == "researcher"


@pytest.mark.asyncio
async def test_detect_specialist_sysadmin(director):
    assert director._detect_specialist("check docker status") == "sysadmin"


@pytest.mark.asyncio
async def test_get_status(director):
    status = director.get_status()
    assert "agents" in status
    assert "director" in status["agents"]


@pytest.mark.asyncio
async def test_get_full_status_no_deadlock(director):
    status = await director.get_full_status()
    assert "memory_episodes" in status
    assert "ecosystem" in status
    # Self should be marked healthy without deadlocking
    eco = status.get("ecosystem", {})
    self_status = eco.get("sahiix-agi", {})
    assert self_status.get("healthy") is True
