"""Specialized agent subclasses with enhanced capabilities and ReAct reasoning."""
import asyncio
from typing import AsyncIterator

from agents.base import Agent, AgentConfig, _TOOL_INSTRUCTIONS
from core.llm import Message


class CoderAgent(Agent):
    """Agent optimized for code generation, review, and file manipulation."""

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", "CODER MODE: Think → Plan → Code → Verify. Always wrap code in ```language blocks."),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))
        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))
        messages.append(Message("user", user_input))
        content = await self._run_with_tools(messages, temperature=0.2, max_tokens=768)
        await self._save_turn(user_input, content)
        return content


class ResearcherAgent(Agent):
    """Agent optimized for research with automatic web search and synthesis."""

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        # Auto-trigger web search for research queries
        search_result = ""
        try:
            result = await self.tools.execute("web_search", query=user_input, max_results=3)
            if result.success and result.output and "No results" not in result.output:
                search_result = f"\n\n[Web Search Results]\n{result.output}"
        except Exception:
            pass

        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", "RESEARCH MODE: Search → Extract → Synthesize → Cite. Use bullet points."),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))
        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))
        messages.append(Message("user", user_input + search_result))
        content = await self._run_with_tools(messages, temperature=0.4)
        await self._save_turn(user_input, content)
        return content


class SysAdminAgent(Agent):
    """Agent optimized for system administration with safe shell execution."""

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", "SYSADMIN MODE: Diagnose → Verify → Act. Use shell, docker_ops, system_info tools."),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))
        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))
        messages.append(Message("user", user_input))
        content = await self._run_with_tools(messages, temperature=0.3)
        await self._save_turn(user_input, content)
        return content


class ArchitectAgent(Agent):
    """Agent optimized for system design and architecture."""

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", "ARCHITECT MODE: Requirements → Constraints → Trade-offs → Design → Diagram."),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))
        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))
        messages.append(Message("user", user_input))
        content = await self._run_with_tools(messages, temperature=0.5)
        await self._save_turn(user_input, content)
        return content


class DataEngineerAgent(Agent):
    """Agent optimized for data engineering: SQL, ETL, pipelines, schema design, and data validation."""

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        # Auto-trigger sql_analysis when SQL-related keywords appear
        analysis_result = ""
        if any(k in user_input.lower() for k in ["sql", "query", "schema", "table", "select", "insert", "update", "create", "alter", "drop"]):
            try:
                result = await self.tools.execute("sql_analysis", sql=user_input, dialect="sqlite")
                if result.success and result.output:
                    analysis_result = f"\n\n[SQL Analysis]\n{result.output}"
            except Exception:
                pass

        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", "DATA ENGINEER MODE: Ingest → Transform → Validate → Load. Think in pipelines and schemas."),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))
        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))
        messages.append(Message("user", user_input + analysis_result))
        content = await self._run_with_tools(messages, temperature=0.3, max_tokens=768)
        await self._save_turn(user_input, content)
        return content


AGENT_CLASS_MAP = {
    "coder": CoderAgent,
    "researcher": ResearcherAgent,
    "sysadmin": SysAdminAgent,
    "architect": ArchitectAgent,
    "dataengineer": DataEngineerAgent,
}
