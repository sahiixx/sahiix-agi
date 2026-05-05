"""Tests for agent layer."""
import pytest
import pytest_asyncio
import asyncio
from agents.base import Agent, AgentConfig
from core.llm import LLMManager, Message
from memory.store import MemoryStore
from tools.registry import ToolRegistry
import tempfile
import os


@pytest_asyncio.fixture
async def agent_deps():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    memory = MemoryStore(db_path)
    await memory.init()

    llm = LLMManager({
        "default_provider": "ollama",
        "providers": {
            "ollama": {"base_url": "http://localhost:11434", "model": "kimi-k2.6:cloud", "timeout": 5}
        },
        "fallback_chain": ["ollama"]
    })
    tools = ToolRegistry({})
    cfg = AgentConfig(name="test", description="test agent", system_prompt="You are a test agent.")
    agent = Agent(cfg, llm, memory, tools)
    yield agent
    await memory.close()
    await llm.close()
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_agent_extract_json_tool_calls(agent_deps):
    agent = agent_deps
    text = 'Let me search. {"tool": "web_search", "params": {"query": "AI news"}}'
    calls = agent._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "web_search"
    assert calls[0][1]["query"] == "AI news"


@pytest.mark.asyncio
async def test_agent_extract_xml_tool_calls(agent_deps):
    agent = agent_deps
    text = 'Let me check. <tool>shell</tool><params>{"command":"ls"}</params>'
    calls = agent._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "shell"


@pytest.mark.asyncio
async def test_agent_extract_mixed_calls(agent_deps):
    agent = agent_deps
    text = '{"tool": "web_search", "params": {"query": "x"}} and <tool>shell</tool><params>{"command":"ls"}</params>'
    calls = agent._extract_tool_calls(text)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_agent_no_tool_calls(agent_deps):
    agent = agent_deps
    text = "Just a regular response without any tools."
    calls = agent._extract_tool_calls(text)
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_agent_save_turn(agent_deps):
    agent = agent_deps
    await agent._save_turn("hello", "hi there")
    await asyncio.sleep(0.3)  # let fire-and-forget tasks complete
    recent = await agent.memory.get_recent(agent="test", limit=5)
    assert len(recent) == 2
    roles = [r["role"] for r in recent]
    assert "user" in roles
    assert "assistant" in roles
