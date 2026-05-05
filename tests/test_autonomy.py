"""Tests for autonomy engine."""
import pytest
import pytest_asyncio
import asyncio
from core.autonomy import SafetyProxy, ToolFabricator, AutonomousEngine, _load_ledger, _save_ledger
import tempfile
import os


def test_safety_proxy_clean():
    sp = SafetyProxy()
    result = sp.scan("Hello world")
    assert result["safe"] is True
    assert result["threat_level"] == "none"


def test_safety_proxy_blocked():
    sp = SafetyProxy()
    result = sp.scan("rm -rf /")
    assert result["safe"] is False
    assert result["threat_level"] == "critical"


def test_safety_proxy_warning():
    sp = SafetyProxy()
    result = sp.scan("eval(something)")
    assert result["safe"] is False
    assert result["threat_level"] == "warning"


@pytest.mark.asyncio
async def test_tool_fabricator_validation():
    from core.llm import LLMManager
    mgr = LLMManager({
        "default_provider": "ollama",
        "providers": {"ollama": {"base_url": "http://localhost:11434", "model": "fake", "timeout": 1}},
        "fallback_chain": ["ollama"]
    })
    tf = ToolFabricator(mgr)
    valid, msg = tf._validate("async def test(): pass")
    assert valid is True
    valid, msg = tf._validate("async def test(): os.system('rm -rf /')")
    assert valid is False
    await mgr.close()


def test_ledger_save_load():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    global LEDGER_PATH
    from core import autonomy
    from pathlib import Path
    old_path = autonomy.LEDGER_PATH
    autonomy.LEDGER_PATH = Path(path)
    ledger = {"explorations": [{"topic": "test", "timestamp": "2024-01-01", "findings": 3}], "topics": {}}
    _save_ledger(ledger)
    loaded = _load_ledger()
    assert loaded["explorations"][0]["topic"] == "test"
    autonomy.LEDGER_PATH = old_path
    os.unlink(path)
