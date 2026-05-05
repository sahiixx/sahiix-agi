"""Ultra-low-latency LLM Provider with connection pooling, circuit breaker, response cache, and structured outputs."""
import asyncio
import json
import time
import hashlib
from typing import AsyncIterator, Dict, Any, Optional
from dataclasses import dataclass

import aiohttp
import orjson


@dataclass
class Message:
    role: str
    content: str
    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Dict[str, Any]
    raw: Dict[str, Any]
    latency_ms: float = 0.0


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.failures >= self.failure_threshold:
            self.state = "open"

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.last_failure_time and (time.monotonic() - self.last_failure_time) > self.recovery_timeout:
                self.state = "half-open"
                return True
            return False
        return True


class BaseProvider:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.breaker = CircuitBreaker(
            failure_threshold=config.get("circuit_breaker_threshold", 3),
            recovery_timeout=config.get("circuit_breaker_timeout", 30.0)
        )
        self._cache: Dict[str, LLMResponse] = {}
        self._cache_ttl = config.get("cache_ttl_seconds", 60)
        self._cache_times: Dict[str, float] = {}
        # Optional distributed Redis cache
        self._redis: Optional[Any] = None
        try:
            from core.redis_cache import AsyncRedisCache
            self._redis = AsyncRedisCache()
        except Exception:
            pass
        # Async semaphore for serialized LLM calls (Ollama is single-threaded)
        sem_value = config.get("max_concurrent_requests", 1)
        self._max_concurrent = max(1, sem_value)
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._sem_lock = asyncio.Lock()

        self.num_ctx = config.get("num_ctx", 4096)
        self.max_tokens = config.get("max_tokens", 512)
        self.temperature = config.get("temperature", 0.4)
        self.timeout = aiohttp.ClientTimeout(
            total=config.get("timeout", 120),
            connect=5.0,
            sock_read=120.0
        )

    async def _ensure_semaphore(self):
        async with self._sem_lock:
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(self._max_concurrent)
            return self._semaphore

    def _get_cache_key(self, messages: list[Message], **kwargs) -> str:
        data = orjson.dumps({"messages": [m.to_dict() for m in messages], "params": kwargs})
        return hashlib.blake2b(data, digest_size=16).hexdigest()

    def _get_cached(self, key: str) -> Optional[LLMResponse]:
        # 1) in-process L1
        if key in self._cache:
            if (time.monotonic() - self._cache_times.get(key, 0)) < self._cache_ttl:
                return self._cache[key]
            del self._cache[key]
            del self._cache_times[key]
        return None

    async def _get_cached_async(self, key: str) -> Optional[LLMResponse]:
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        # 2) attempt Redis L2
        if self._redis is not None:
            try:
                data = await self._redis.get(key)
                if data is not None:
                    parsed = orjson.loads(data)
                    response = LLMResponse(**parsed)
                    self._set_cached(key, response)  # backfill L1
                    return response
            except Exception:
                pass
        return None

    def _set_cached(self, key: str, response: LLMResponse):
        self._cache[key] = response
        self._cache_times[key] = time.monotonic()

    async def _set_cached_async(self, key: str, response: LLMResponse):
        self._set_cached(key, response)
        if self._redis is not None:
            try:
                payload = orjson.dumps({
                    "content": response.content,
                    "model": response.model,
                    "usage": response.usage,
                    "latency_ms": response.latency_ms,
                })
                await self._redis.set(key, payload, ttl=self._cache_ttl)
            except Exception:
                pass

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(
                limit=20, limit_per_host=10,
                keepalive_timeout=60.0, enable_cleanup_closed=True, force_close=False
            )
            self.session = aiohttp.ClientSession(
                connector=connector, timeout=self.timeout,
                headers={"Connection": "keep-alive"}
            )
        return self.session

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        raise NotImplementedError

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class OllamaProvider(BaseProvider):
    async def _warm_connection(self):
        try:
            session = await self._get_session()
            async with session.head(
                f"{self.config['base_url']}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3)
            ):
                pass
        except Exception:
            pass

    async def _load_model(self):
        """Pre-load model into memory to reduce first-request latency."""
        try:
            session = await self._get_session()
            payload = {
                "model": self.config.get("model", "kimi-k2.6:cloud"),
                "keep_alive": "30m"
            }
            async with session.post(
                f"{self.config['base_url']}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ):
                pass
        except Exception:
            pass

    async def embed(self, text: str, model: str = None) -> list[float]:
        session = await self._get_session()
        embed_model = model or self.config.get("embed_model", "nomic-embed-text")
        payload = {"model": embed_model, "prompt": text}
        try:
            async with session.post(
                f"{self.config['base_url']}/api/embeddings",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                data = await resp.json()
                return data.get("embedding", [])
        except Exception:
            return []

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — LLM temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0)
        cache_key = self._get_cache_key(messages, **kwargs)
        cached = await self._get_cached_async(cache_key)
        if cached:
            return cached

        session = await self._get_session()
        url = f"{self.config['base_url']}/api/chat"
        payload = {
            "model": self.config.get("model", "kimi-k2.6:cloud"),
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
                "num_ctx": kwargs.get("num_ctx", self.num_ctx),
                "num_predict": kwargs.get("max_tokens", self.max_tokens),
            },
            "keep_alive": "30m"
        }
        if kwargs.get("json_mode"):
            payload["format"] = "json"

        start = time.monotonic()
        sem = await self._ensure_semaphore()
        async with sem:
            try:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    data = await resp.json()
                    latency = (time.monotonic() - start) * 1000
                    raw_content = data.get("message", {}).get("content", "")
                    if not raw_content and data.get("message", {}).get("thinking"):
                        raw_content = data.get("message", {}).get("thinking", "")
                    response = LLMResponse(
                        content=raw_content,
                        model=data.get("model", "unknown"),
                        usage={},
                        raw=data,
                        latency_ms=latency
                    )
                    self.breaker.record_success()
                    await self._set_cached_async(cache_key, response)
                    return response
            except asyncio.TimeoutError:
                self.breaker.record_failure()
                return LLMResponse(
                    content="[LLM Timeout — request took too long]",
                    model="error", usage={}, raw={},
                    latency_ms=(time.monotonic() - start) * 1000
                )
            except Exception as e:
                self.breaker.record_failure()
                return LLMResponse(
                    content=f"[LLM Error: {type(e).__name__}: {e}]",
                    model="error", usage={}, raw={},
                    latency_ms=(time.monotonic() - start) * 1000
                )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        if not self.breaker.can_execute():
            yield "[Circuit breaker open — LLM temporarily unavailable]"
            return

        session = await self._get_session()
        url = f"{self.config['base_url']}/api/chat"
        payload = {
            "model": self.config.get("model", "kimi-k2.6:cloud"),
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "options": {
                "temperature": kwargs.get("temperature", self.temperature),
                "num_ctx": kwargs.get("num_ctx", self.num_ctx),
                "num_predict": kwargs.get("max_tokens", self.max_tokens),
            },
            "keep_alive": "30m"
        }
        try:
            async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                async for line in resp.content:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("done"):
                            break
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            yield chunk
                    except json.JSONDecodeError:
                        continue
        except asyncio.TimeoutError:
            yield "[Stream Timeout — connection stalled]"
        except Exception as e:
            yield f"[Stream Error: {type(e).__name__}]"


class OpenAICompatibleProvider(BaseProvider):
    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — LLM temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0
            )
        cache_key = self._get_cache_key(messages, **kwargs)
        cached = await self._get_cached_async(cache_key)
        if cached:
            return cached

        session = await self._get_session()
        url = f"{self.config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.get('api_key', '')}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.config.get("model", "gpt-4o-mini"),
            "messages": [m.to_dict() for m in messages],
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens)
        }
        if kwargs.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                latency = (time.monotonic() - start) * 1000
                choice = data.get("choices", [{}])[0]
                response = LLMResponse(
                    content=choice.get("message", {}).get("content", ""),
                    model=data.get("model", "unknown"),
                    usage=data.get("usage", {}),
                    raw=data,
                    latency_ms=latency
                )
                self.breaker.record_success()
                await self._set_cached_async(cache_key, response)
                return response
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return LLMResponse(
                content="[LLM Timeout — request took too long]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as e:
            self.breaker.record_failure()
            return LLMResponse(
                content=f"[LLM Error: {type(e).__name__}: {e}]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        if not self.breaker.can_execute():
            yield "[Circuit breaker open — LLM temporarily unavailable]"
            return

        session = await self._get_session()
        url = f"{self.config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.get('api_key', '')}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.config.get("model", "gpt-4o-mini"),
            "messages": [m.to_dict() for m in messages],
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": True
        }
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if chunk:
                                yield chunk
                        except json.JSONDecodeError:
                            continue
        except asyncio.TimeoutError:
            yield "[Stream Timeout — connection stalled]"
        except Exception as e:
            yield f"[Stream Error: {type(e).__name__}]"


# ── Gemini Provider (Google) ──────────────────────────────────────────────────

class GeminiProvider(BaseProvider):
    """Google Gemini 2.5 Flash via Generative Language API."""

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — Gemini temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0
            )
        cache_key = self._get_cache_key(messages, **kwargs)
        cached = await self._get_cached_async(cache_key)
        if cached:
            return cached

        session = await self._get_session()
        model = self.config.get("model", "gemini-2.5-flash")
        url = f"{self.config['base_url']}/v1beta/models/{model}:generateContent"
        headers = {
            "x-goog-api-key": self.config.get("api_key", ""),
            "Content-Type": "application/json"
        }

        # Gemini uses content/parts format
        gemini_messages = []
        for m in messages:
            role = "user" if m.role in ("user", "system") else "model"
            gemini_messages.append({"role": role, "parts": [{"text": m.content}]})

        payload = {
            "contents": gemini_messages,
            "generationConfig": {
                "temperature": kwargs.get("temperature", self.temperature),
                "maxOutputTokens": kwargs.get("max_tokens", self.max_tokens),
                "responseMimeType": "application/json" if kwargs.get("json_mode") else "text/plain"
            }
        }

        start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                latency = (time.monotonic() - start) * 1000
                candidates = data.get("candidates", [{}])
                text = ""
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)
                response = LLMResponse(
                    content=text,
                    model=data.get("model", model),
                    usage=data.get("usageMetadata", {}),
                    raw=data,
                    latency_ms=latency
                )
                self.breaker.record_success()
                await self._set_cached_async(cache_key, response)
                return response
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return LLMResponse(
                content="[Gemini Timeout — request took too long]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as e:
            self.breaker.record_failure()
            return LLMResponse(
                content=f"[Gemini Error: {type(e).__name__}: {e}]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        yield "[Gemini streaming not yet implemented]"


# ── Claude Provider (Anthropic) ───────────────────────────────────────────────

class ClaudeProvider(BaseProvider):
    """Anthropic Claude 4 via Messages API."""

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — Claude temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0
            )
        cache_key = self._get_cache_key(messages, **kwargs)
        cached = await self._get_cached_async(cache_key)
        if cached:
            return cached

        session = await self._get_session()
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.config.get("api_key", ""),
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        # Extract system prompt, rest as messages
        system_text = ""
        claude_messages = []
        for m in messages:
            if m.role == "system":
                system_text += m.content + "\n"
            else:
                claude_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": self.config.get("model", "claude-4-sonnet-20250501"),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "messages": claude_messages,
            "stream": False
        }
        if system_text:
            payload["system"] = system_text.strip()
        if kwargs.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                latency = (time.monotonic() - start) * 1000
                content_blocks = data.get("content", [])
                text = ""
                for block in content_blocks:
                    if block.get("type") == "text":
                        text += block.get("text", "")
                response = LLMResponse(
                    content=text,
                    model=data.get("model", "unknown"),
                    usage=data.get("usage", {}),
                    raw=data,
                    latency_ms=latency
                )
                self.breaker.record_success()
                await self._set_cached_async(cache_key, response)
                return response
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return LLMResponse(
                content="[Claude Timeout — request took too long]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as e:
            self.breaker.record_failure()
            return LLMResponse(
                content=f"[Claude Error: {type(e).__name__}: {e}]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        if not self.breaker.can_execute():
            yield "[Circuit breaker open — Claude temporarily unavailable]"
            return
        yield "[Claude streaming not yet implemented]"


# ── DeepSeek Provider ──────────────────────────────────────────────────────────

class DeepSeekProvider(BaseProvider):
    """DeepSeek-V4 via OpenAI-compatible API."""

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — DeepSeek temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0
            )
        cache_key = self._get_cache_key(messages, **kwargs)
        cached = await self._get_cached_async(cache_key)
        if cached:
            return cached

        session = await self._get_session()
        url = f"{self.config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.get('api_key', '')}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.config.get("model", "deepseek-v4"),
            "messages": [m.to_dict() for m in messages],
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens)
        }
        if kwargs.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                latency = (time.monotonic() - start) * 1000
                choice = data.get("choices", [{}])[0]
                response = LLMResponse(
                    content=choice.get("message", {}).get("content", ""),
                    model=data.get("model", "unknown"),
                    usage=data.get("usage", {}),
                    raw=data,
                    latency_ms=latency
                )
                self.breaker.record_success()
                await self._set_cached_async(cache_key, response)
                return response
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return LLMResponse(
                content="[DeepSeek Timeout — request took too long]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as e:
            self.breaker.record_failure()
            return LLMResponse(
                content=f"[DeepSeek Error: {type(e).__name__}: {e}]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        if not self.breaker.can_execute():
            yield "[Circuit breaker open — DeepSeek temporarily unavailable]"
            return
        yield "[DeepSeek streaming not yet implemented]"


# ── vLLM Provider (Self-hosted GPU Inference) ─────────────────────────────────

class VLLMProvider(BaseProvider):
    """vLLM self-hosted inference with OpenAI-compatible API."""

    async def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if not self.breaker.can_execute():
            return LLMResponse(
                content="[Circuit breaker open — vLLM temporarily unavailable]",
                model="error", usage={}, raw={}, latency_ms=0
            )
        session = await self._get_session()
        url = f"{self.config['base_url']}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.get('api_key', 'vllm')}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.config.get("model", "default"),
            "messages": [m.to_dict() for m in messages],
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens)
        }

        start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                latency = (time.monotonic() - start) * 1000
                choice = data.get("choices", [{}])[0]
                response = LLMResponse(
                    content=choice.get("message", {}).get("content", ""),
                    model=data.get("model", "unknown"),
                    usage=data.get("usage", {}),
                    raw=data,
                    latency_ms=latency
                )
                self.breaker.record_success()
                return response
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return LLMResponse(
                content="[vLLM Timeout — request took too long]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as e:
            self.breaker.record_failure()
            return LLMResponse(
                content=f"[vLLM Error: {type(e).__name__}: {e}]",
                model="error", usage={}, raw={},
                latency_ms=(time.monotonic() - start) * 1000
            )

    async def stream_chat(self, messages: list[Message], **kwargs) -> AsyncIterator[str]:
        yield "[vLLM streaming not yet implemented]"


class LLMManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.providers: Dict[str, BaseProvider] = {}
        self._init_providers()

    def _init_providers(self):
        for name, cfg in self.config.get("providers", {}).items():
            if name == "ollama":
                self.providers[name] = OllamaProvider(cfg)
            elif name == "gemini":
                self.providers[name] = GeminiProvider(cfg)
            elif name == "claude":
                self.providers[name] = ClaudeProvider(cfg)
            elif name == "deepseek":
                self.providers[name] = DeepSeekProvider(cfg)
            elif name == "vllm":
                self.providers[name] = VLLMProvider(cfg)
            else:
                self.providers[name] = OpenAICompatibleProvider(cfg)

    async def warmup(self):
        tasks = []
        for provider in self.providers.values():
            if hasattr(provider, '_warm_connection'):
                tasks.append(provider._warm_connection())
            if hasattr(provider, '_load_model'):
                tasks.append(provider._load_model())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def embed(self, text: str, provider: Optional[str] = None) -> list[float]:
        prov_name = provider or self.config.get("default_provider", "ollama")
        if prov_name not in self.providers:
            for fallback in self.config.get("fallback_chain", []):
                if fallback in self.providers:
                    prov_name = fallback
                    break
        p = self.providers[prov_name]
        if hasattr(p, 'embed'):
            return await p.embed(text)
        return []

    async def chat(self, messages: list[Message], provider: Optional[str] = None, **kwargs) -> LLMResponse:
        prov_name = provider or self.config.get("default_provider", "ollama")
        if prov_name not in self.providers:
            for fallback in self.config.get("fallback_chain", []):
                if fallback in self.providers:
                    prov_name = fallback
                    break
        return await self.providers[prov_name].chat(messages, **kwargs)

    async def stream_chat(self, messages: list[Message], provider: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        prov_name = provider or self.config.get("default_provider", "ollama")
        if prov_name not in self.providers:
            for fallback in self.config.get("fallback_chain", []):
                if fallback in self.providers:
                    prov_name = fallback
                    break
        async for chunk in self.providers[prov_name].stream_chat(messages, **kwargs):
            yield chunk

    async def close(self):
        await asyncio.gather(*[p.close() for p in self.providers.values()], return_exceptions=True)
