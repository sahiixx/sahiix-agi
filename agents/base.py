"""Base Agent classes for SAHIIX AGI — ReAct-enabled with structured tool use."""
import asyncio
import json
import re
from typing import Dict, Any, AsyncIterator
from dataclasses import dataclass

import orjson

from core.llm import LLMManager, Message
from memory.store import MemoryStore
from tools.registry import ToolRegistry


@dataclass
class AgentConfig:
    name: str
    description: str
    system_prompt: str
    priority: int = 0


# Universal tool-use instructions appended to every agent
_TOOL_INSTRUCTIONS = """
TOOL USE RULES:
1. When you need to act, output a tool call in ONE of these formats:
   JSON (preferred): {"tool": "tool_name", "params": {"key": "value"}}
   XML fallback: <tool>tool_name</tool><params>{"key":"value"}</params>
2. You may chain multiple tool calls. Each call will be executed and results appended.
3. After receiving tool results, synthesize a final answer for the user.
4. If no tool is needed, answer directly.
"""


class Agent:
    def __init__(self, config: AgentConfig, llm: LLMManager, memory: MemoryStore, tools: ToolRegistry):
        self.config = config
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self._warm_cache: Dict[str, Any] = {}

    async def _run_with_tools(self, messages: list[Message], temperature: float = 0.4, max_tokens: int = None) -> str:
        """Execute a multi-turn ReAct loop with tool calling."""
        content = ""
        kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        for iteration in range(5):
            response = await self.llm.chat(messages, **kwargs)
            turn_content = response.content

            handled, had_calls = await self._handle_tool_calls(turn_content)
            if not had_calls:
                content = handled
                break

            messages.append(Message("assistant", turn_content))
            messages.append(Message("user", f"Tool results:\n{handled}"))
            content = handled

        return content

    async def run(self, user_input: str, context: str = "", use_cache: bool = True) -> str:
        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("system", f"Available tools: {', '.join(t['name'] for t in self.tools.list_tools())}"),
        ]
        if context:
            messages.append(Message("system", f"Context: {context}"))

        recent = await self.memory.get_recent(agent=self.config.name, limit=5)
        for ep in reversed(recent):
            messages.append(Message(ep["role"], ep["content"]))

        messages.append(Message("user", user_input))

        content = await self._run_with_tools(messages, temperature=0.4)
        await self._save_turn(user_input, content)
        return content

    async def _save_turn(self, user_input: str, content: str):
        """Save episode and generate embeddings fire-and-forget with error logging."""
        user_ep = asyncio.create_task(self.memory.save_episode("user", user_input, agent=self.config.name))
        assistant_ep = asyncio.create_task(self.memory.save_episode("assistant", content, agent=self.config.name))

        async def _embed():
            try:
                uid = await user_ep
                aid = await assistant_ep
                if hasattr(self.llm, 'embed') and uid > 0:
                    uvec = await self.llm.embed(user_input)
                    if uvec:
                        await self.memory.save_embedding(uid, uvec, model="embed")
                if hasattr(self.llm, 'embed') and aid > 0:
                    avec = await self.llm.embed(content)
                    if avec:
                        await self.memory.save_embedding(aid, avec, model="embed")
            except Exception as e:
                print(f"[Agent {self.config.name}] Embedding error: {e}")
        asyncio.create_task(_embed())

    async def stream(self, user_input: str, context: str = "") -> AsyncIterator[str]:
        """Stream response and capture full text for memory persistence."""
        messages = [
            Message("system", self.config.system_prompt + _TOOL_INSTRUCTIONS),
            Message("user", user_input),
        ]
        full_text_parts = []
        async for chunk in self.llm.stream_chat(messages):
            full_text_parts.append(chunk)
            yield chunk

        full_text = "".join(full_text_parts)
        # Save both user input and complete assistant response
        await self.memory.save_episode("user", user_input, agent=self.config.name)
        await self.memory.save_episode("assistant", full_text, agent=self.config.name)

    async def _handle_tool_calls(self, content: str) -> tuple[str, bool]:
        """Parse and execute JSON or XML tool calls. Returns (content_with_results, had_calls)."""
        max_iterations = 3
        iteration = 0
        had_calls = False
        current = content

        while iteration < max_iterations:
            iteration += 1
            calls = self._extract_tool_calls(current)
            if not calls:
                break

            had_calls = True
            tasks = []
            for tool_name, params in calls:
                tasks.append(self.tools.execute(tool_name, **params))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            result_texts = []
            for (tool_name, _), result in zip(calls, results):
                if isinstance(result, Exception):
                    result_texts.append(f"[Tool: {tool_name}] Error: {result}")
                else:
                    result_texts.append(
                        f"[Tool: {tool_name}]\nSuccess: {result.success}\nOutput: {result.output}\nError: {result.error}"
                    )

            current = current + "\n\n" + "\n---\n".join(result_texts)
            break

        return current, had_calls

    def _extract_tool_calls(self, content: str) -> list[tuple[str, dict]]:
        """Extract tool calls from JSON or XML formats."""
        calls = []

        # 1. Try JSON format: {"tool": "name", "params": {...}}
        # Scan for top-level JSON objects and parse them.
        i = 0
        while i < len(content):
            if content[i] != '{':
                i += 1
                continue
            # Brace-count to find matching }
            start = i
            brace_depth = 0
            in_string = False
            escape_next = False
            end = i
            while end < len(content):
                ch = content[end]
                if escape_next:
                    escape_next = False
                elif ch == '\\' and in_string:
                    escape_next = True
                elif ch == '"':
                    in_string = not in_string
                elif not in_string:
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            end += 1
                            break
                end += 1
            obj_str = content[start:end]
            try:
                obj = orjson.loads(obj_str.encode())
                if isinstance(obj, dict) and "tool" in obj and isinstance(obj["tool"], str):
                    calls.append((obj["tool"], obj.get("params", {})))
            except Exception:
                pass
            i = end if end > start else start + 1

        # 2. Try XML fallback
        xml_pattern = r"<tool>(\w+)</tool>\s*<params>(.*?)</params>"
        for match in re.finditer(xml_pattern, content, re.DOTALL):
            tool_name = match.group(1)
            params_str = match.group(2)
            try:
                params = orjson.loads(params_str.encode()) if params_str.strip() else {}
                calls.append((tool_name, params))
            except Exception:
                pass

        return calls
