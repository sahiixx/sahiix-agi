"""Tests for memory store layer."""
import pytest
import pytest_asyncio
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock
from memory.store import MemoryStore


@pytest_asyncio.fixture
async def memory():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = MemoryStore(path)
    await store.init()
    # disable qdrant so vector_search falls back to cosine via sqlite
    store._qdrant = None
    store.qdrant_url = None
    yield store
    await store.close()
    os.unlink(path)


@pytest.mark.asyncio
async def test_save_and_get_recent(memory):
    ep_id = await memory.save_episode("user", "hello", agent="director")
    assert ep_id > 0
    recent = await memory.get_recent(agent="director", limit=5)
    assert len(recent) == 1
    assert recent[0]["content"] == "hello"
    assert recent[0]["role"] == "user"


@pytest.mark.asyncio
async def test_search(memory):
    await memory.save_episode("user", "python programming", agent="coder")
    await memory.save_episode("assistant", "code review done", agent="coder")
    results = await memory.search("python", limit=5)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_embedding_save_and_search(memory):
    ep_id = await memory.save_episode("user", "test", agent="director")
    vec = [0.1, 0.2, 0.3, 0.4]
    await memory.save_embedding(ep_id, vec, model="test")
    # because qdrant is stubbed, vector_search falls back to cosine via sqlite
    results = await memory.vector_search(vec, limit=5)
    assert len(results) == 1
    assert results[0]["_score"] > 0.99


@pytest.mark.asyncio
async def test_fact_crud(memory):
    await memory.save_fact("key1", "value1", source="test")
    val = await memory.get_fact("key1")
    assert val == "value1"


@pytest.mark.asyncio
async def test_conversation_save(memory):
    cid = await memory.save_conversation("test", [{"role": "user", "content": "hi"}])
    assert cid > 0
    convs = await memory.get_conversations(limit=5)
    assert len(convs) == 1
