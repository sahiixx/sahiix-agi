"""Tests for core LLM provider layer."""
import pytest
import pytest_asyncio
import asyncio
from core.llm import CircuitBreaker, Message, OllamaProvider, LLMManager


def test_circuit_breaker_closed_by_default():
    cb = CircuitBreaker()
    assert cb.can_execute() is True
    assert cb.state == "closed"


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.can_execute() is True  # still half-open after 2
    cb.record_failure()
    assert cb.can_execute() is False  # open after 3


def test_circuit_breaker_recover():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    # Wait for recovery timeout
    import time
    time.sleep(0.02)
    assert cb.can_execute() is True
    assert cb.state == "half-open"


def test_message_to_dict():
    m = Message("user", "hello")
    assert m.to_dict() == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_llm_manager_init():
    config = {
        "default_provider": "ollama",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434",
                "model": "kimi-k2.6:cloud",
                "timeout": 5,
            }
        },
        "fallback_chain": ["ollama"]
    }
    mgr = LLMManager(config)
    assert "ollama" in mgr.providers
    await mgr.close()


@pytest.mark.asyncio
async def test_ollama_provider_error_handling():
    cfg = {"base_url": "http://localhost:99999", "model": "fake", "timeout": 1}
    prov = OllamaProvider(cfg)
    resp = await prov.chat([Message("user", "hi")])
    assert "error" in resp.content.lower() or "timeout" in resp.content.lower() or "cannot connect" in resp.content.lower()
    await prov.close()
