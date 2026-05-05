"""Tests for tool registry and security."""
import pytest
import pytest_asyncio
import asyncio
from tools.registry import ToolRegistry, ToolResult


@pytest_asyncio.fixture
def registry():
    cfg = {
        "shell": {"allowed_commands": ["ls", "echo"], "blocked_patterns": ["rm -rf /"]},
        "file_write": {"allowed_base_dirs": ["/tmp"]},
    }
    return ToolRegistry(cfg)


@pytest.mark.asyncio
async def test_list_tools(registry):
    tools = registry.list_tools()
    names = [t["name"] for t in tools]
    assert "shell" in names
    assert "file_read" in names
    assert "web_search" in names


@pytest.mark.asyncio
async def test_shell_allowed(registry):
    result = await registry.execute("shell", command="echo hello")
    assert result.success is True
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_shell_blocked_command(registry):
    result = await registry.execute("shell", command="rm -rf /")
    assert result.success is False
    assert "Blocked" in result.error or "not in allowed" in result.error


@pytest.mark.asyncio
async def test_shell_not_allowed(registry):
    result = await registry.execute("shell", command="whoami")
    assert result.success is False
    assert "not in allowed" in result.error


@pytest.mark.asyncio
async def test_file_write_path_sanitization(registry):
    result = await registry.execute("file_write", path="/etc/passwd", content="bad")
    assert result.success is False
    assert "Security" in result.error or "outside allowed" in result.error


@pytest.mark.asyncio
async def test_file_write_allowed(registry):
    import tempfile
    path = tempfile.mktemp(suffix=".txt", dir="/tmp")
    result = await registry.execute("file_write", path=path, content="hello")
    assert result.success is True
    # Cleanup
    import os
    os.unlink(path)


@pytest.mark.asyncio
async def test_system_info(registry):
    result = await registry.execute("system_info")
    assert result.success is True
    assert "platform" in result.output


@pytest.mark.asyncio
async def test_performance_metrics(registry):
    result = await registry.execute("performance_metrics")
    assert result.success is True
    assert "cpu_percent" in result.output


@pytest.mark.asyncio
async def test_python_exec_blocked(registry):
    result = await registry.execute("python_exec", code="import os")
    assert result.success is False
    assert "Blocked" in result.error


@pytest.mark.asyncio
async def test_python_exec_safe(registry):
    result = await registry.execute("python_exec", code="1 + 1")
    assert result.success is True


@pytest.mark.asyncio
async def test_git_ops_blocked(registry):
    result = await registry.execute("git_ops", command="push origin main")
    assert result.success is False


@pytest.mark.asyncio
async def test_docker_ops_blocked(registry):
    result = await registry.execute("docker_ops", command="run hello")
    assert result.success is False


@pytest.mark.asyncio
async def test_http_request(registry):
    result = await registry.execute("http_request", url="https://httpbin.org/get", timeout=10)
    assert result.success is True
