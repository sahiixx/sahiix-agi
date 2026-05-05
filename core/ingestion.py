"""Real-time data ingestion layer: RSS, webhooks, API polling, file watching."""
import asyncio
import hashlib
import hmac
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp
import feedparser
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from core.autonomy import Event, EventBus

# ── Optional Kafka producer ─────────────────────────────────────────────────
try:
    from aiokafka import AIOKafkaProducer
    _KAFKA_AVAILABLE = True
except Exception:
    _KAFKA_AVAILABLE = False


class _KafkaPublisher:
    """Lazy Kafka publisher that connects on first emit."""
    def __init__(self, bootstrap_servers: str = "localhost:9092", topic: str = "sahiix-events"):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self._producer: Optional[Any] = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if not _KAFKA_AVAILABLE or self._producer:
            return
        async with self._lock:
            if self._producer:
                return
            try:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode()
                )
                await self._producer.start()
            except Exception:
                self._producer = None

    async def emit(self, event: dict):
        if not _KAFKA_AVAILABLE:
            return
        await self._ensure()
        if self._producer:
            try:
                await self._producer.send(self.topic, event)
            except Exception:
                pass

    async def close(self):
        if self._producer:
            try:
                await self._producer.stop()
            except Exception:
                pass


# ── Shared event emission helper ──────────────────────────────────────────────

def _event_payload(source: str, subtype: str, data: dict) -> dict:
    return {
        "source": source,
        "subtype": subtype,
        "data": data,
        "timestamp": time.time(),
    }


# ── RSS Feed Monitor ──────────────────────────────────────────────────────────

@dataclass
class FeedConfig:
    url: str
    interval_seconds: int = 300
    last_etag: str = ""
    last_modified: str = ""
    seen_ids: Set[str] = field(default_factory=set)


class RSSFeedMonitor:
    """Poll RSS/Atom feeds and emit new entries to the event bus."""

    def __init__(
        self,
        bus: EventBus,
        kafka: Optional[_KafkaPublisher] = None,
        feeds: Optional[List[FeedConfig]] = None
    ):
        self.bus = bus
        self.kafka = kafka
        self.feeds: Dict[str, FeedConfig] = {}
        self._tasks: List[asyncio.Task] = []
        self._running = False
        if feeds:
            for f in feeds:
                self.add_feed(f.url, f.interval_seconds)

    def add_feed(self, url: str, interval_seconds: int = 300) -> FeedConfig:
        cfg = FeedConfig(url=url, interval_seconds=interval_seconds)
        self.feeds[url] = cfg
        if self._running:
            self._tasks.append(asyncio.create_task(self._poll_loop(cfg)))
        return cfg

    def remove_feed(self, url: str) -> bool:
        if url in self.feeds:
            del self.feeds[url]
            return True
        return False

    async def start(self):
        if self._running:
            return
        self._running = True
        for cfg in self.feeds.values():
            self._tasks.append(asyncio.create_task(self._poll_loop(cfg)))

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self.kafka:
            await self.kafka.close()

    async def _poll_loop(self, cfg: FeedConfig):
        while self._running:
            try:
                await self._fetch(cfg)
            except Exception as e:
                await self.bus.publish(Event("ingestion.rss.error", {"url": cfg.url, "error": str(e)}))
            await asyncio.sleep(cfg.interval_seconds)

    async def _fetch(self, cfg: FeedConfig):
        headers = {}
        if cfg.last_etag:
            headers["If-None-Match"] = cfg.last_etag
        if cfg.last_modified:
            headers["If-Modified-Since"] = cfg.last_modified

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(cfg.url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 304:
                        return
                    body = await resp.text()
                    cfg.last_etag = resp.headers.get("ETag", "")
                    cfg.last_modified = resp.headers.get("Last-Modified", "")
        except Exception as e:
            await self.bus.publish(Event("ingestion.rss.fetch_error", {"url": cfg.url, "error": str(e)}))
            return

        parsed = feedparser.parse(body)
        for entry in parsed.entries:
            entry_id = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not entry_id:
                continue
            if entry_id in cfg.seen_ids:
                continue
            cfg.seen_ids.add(entry_id)
            # Keep seen set bounded
            if len(cfg.seen_ids) > 2000:
                cfg.seen_ids = set(list(cfg.seen_ids)[-1000:])

            payload = _event_payload("rss", "new_entry", {
                "feed": cfg.url,
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": entry.get("summary", "")[:500],
            })
            await self.bus.publish(Event("ingestion.rss", payload))
            if self.kafka:
                await self.kafka.emit(payload)


# ── Webhook Receiver ────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/webhook", tags=["webhooks"])

# Global registry for webhook verification secrets (source -> secret)
WEBHOOK_SECRETS: Dict[str, str] = {}
WEBHOOK_CALLBACKS: Dict[str, List[Callable]] = {}


def _register_webhook_secret(source: str, secret: str):
    WEBHOOK_SECRETS[source] = secret


def _verify_signature(source: str, body: bytes, signature: Optional[str], algo: str = "sha256") -> bool:
    secret = WEBHOOK_SECRETS.get(source)
    if not secret:
        return True  # No secret configured = accept
    if not signature:
        return False
    expected = ""
    if algo == "sha256":
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if algo == "sha1":
        expected = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _emit_webhook_event(source: str, payload: dict, bus: EventBus, kafka: Optional[_KafkaPublisher] = None):
    event = Event("ingestion.webhook", _event_payload("webhook", source, payload))
    await bus.publish(event)
    if kafka:
        await kafka.emit(event.payload)


@router.post("/{source}")
async def receive_webhook(source: str, request: Request, x_hub_signature_256: Optional[str] = Header(None), x_stripe_signature: Optional[str] = Header(None)):
    """Generic webhook endpoint with source-based signature validation."""
    body = await request.body()
    if source == "github":
        if not _verify_signature("github", body, x_hub_signature_256, algo="sha256"):
            raise HTTPException(status_code=401, detail="Invalid signature")
    elif source == "stripe":
        if not _verify_signature("stripe", body, x_stripe_signature, algo="sha1"):
            raise HTTPException(status_code=401, detail="Invalid signature")
    # generic has no signature validation unless a secret is configured

    try:
        payload = json.loads(body)
    except Exception:
        payload = {"raw": body.decode("utf-8", errors="ignore")}

    payload["_webhook_source"] = source
    payload["_received_at"] = time.time()

    # Resolve event bus and kafka from app state
    bus: Optional[EventBus] = getattr(request.app.state, "event_bus", None)
    kafka: Optional[_KafkaPublisher] = getattr(request.app.state, "kafka_publisher", None)
    if bus:
        await _emit_webhook_event(source, payload, bus, kafka)

    # Invoke registered callbacks
    for cb in WEBHOOK_CALLBACKS.get(source, []):
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb(source, payload)
            else:
                cb(source, payload)
        except Exception:
            pass

    return JSONResponse({"received": True, "source": source})


@router.post("/generic")
async def receive_generic_webhook(request: Request):
    """Unnamed generic webhook endpoint."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        payload = {"raw": body.decode("utf-8", errors="ignore")}
    payload["_webhook_source"] = "generic"
    payload["_received_at"] = time.time()
    bus: Optional[EventBus] = getattr(request.app.state, "event_bus", None)
    kafka: Optional[_KafkaPublisher] = getattr(request.app.state, "kafka_publisher", None)
    if bus:
        await _emit_webhook_event("generic", payload, bus, kafka)
    for cb in WEBHOOK_CALLBACKS.get("generic", []):
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb("generic", payload)
            else:
                cb("generic", payload)
        except Exception:
            pass
    return JSONResponse({"received": True, "source": "generic"})


# ── API Poller ────────────────────────────────────────────────────────────────

@dataclass
class PollerConfig:
    name: str
    url: str
    interval_seconds: int = 60
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None
    etag: str = ""
    last_modified: str = ""
    last_checksum: str = ""


class APIPoller:
    """Generic async REST API poller with backoff, rate limiting, and change detection."""

    def __init__(
        self,
        bus: EventBus,
        kafka: Optional[_KafkaPublisher] = None,
        pollers: Optional[List[PollerConfig]] = None
    ):
        self.bus = bus
        self.kafka = kafka
        self.pollers: Dict[str, PollerConfig] = {}
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._backoff: Dict[str, float] = {}
        if pollers:
            for p in pollers:
                self.add(p)

    def add(self, config: PollerConfig) -> PollerConfig:
        self.pollers[config.name] = config
        if self._running:
            self._tasks.append(asyncio.create_task(self._poll_loop(config)))
        return config

    def remove(self, name: str) -> bool:
        return self.pollers.pop(name, None) is not None

    async def start(self):
        if self._running:
            return
        self._running = True
        for p in self.pollers.values():
            self._tasks.append(asyncio.create_task(self._poll_loop(p)))

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _poll_loop(self, cfg: PollerConfig):
        while self._running:
            delay = self._backoff.get(cfg.name, 0)
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                changed = await self._fetch(cfg)
                if changed:
                    self._backoff[cfg.name] = 0
                else:
                    self._backoff[cfg.name] = min((self._backoff.get(cfg.name, 0) or 0) + 1, 5)
            except Exception as e:
                await self.bus.publish(Event("ingestion.poller.error", {"name": cfg.name, "error": str(e)}))
                self._backoff[cfg.name] = min((self._backoff.get(cfg.name, 0) or 0) + 2, 60)
            await asyncio.sleep(cfg.interval_seconds + self._backoff.get(cfg.name, 0))

    async def _fetch(self, cfg: PollerConfig) -> bool:
        headers = dict(cfg.headers)
        if cfg.etag:
            headers["If-None-Match"] = cfg.etag
        if cfg.last_modified:
            headers["If-Modified-Since"] = cfg.last_modified

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            async with session.request(
                cfg.method, cfg.url, headers=headers, data=cfg.body, timeout=timeout
            ) as resp:
                if resp.status == 304:
                    return False
                text = await resp.text()
                cfg.etag = resp.headers.get("ETag", "")
                cfg.last_modified = resp.headers.get("Last-Modified", "")

        checksum = hashlib.sha256(text.encode()).hexdigest()
        if checksum == cfg.last_checksum:
            return False
        cfg.last_checksum = checksum

        payload = _event_payload("api", cfg.name, {
            "url": cfg.url,
            "status": resp.status,
            "checksum": checksum,
            "body_preview": text[:2000],
        })
        await self.bus.publish(Event("ingestion.api", payload))
        if self.kafka:
            await self.kafka.emit(payload)
        return True


# ── File Watcher ─────────────────────────────────────────────────────────────

class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, bus: EventBus, kafka: Optional[_KafkaPublisher] = None, watched_dirs: Optional[List[str]] = None):
        self.bus = bus
        self.kafka = kafka
        self.watched_dirs = set(watched_dirs or [])

    def on_created(self, event):
        if event.is_directory:
            return
        payload = _event_payload("file", "created", {
            "path": event.src_path,
            "is_directory": event.is_directory,
        })
        asyncio.create_task(self._emit(payload))

    def on_modified(self, event):
        if event.is_directory:
            return
        payload = _event_payload("file", "modified", {
            "path": event.src_path,
            "is_directory": event.is_directory,
        })
        asyncio.create_task(self._emit(payload))

    async def _emit(self, payload: dict):
        await self.bus.publish(Event("ingestion.file", payload))
        if self.kafka:
            await self.kafka.emit(payload)


class FileWatcher:
    """Watch directories for new/modified files and emit events."""

    def __init__(
        self,
        bus: EventBus,
        kafka: Optional[_KafkaPublisher] = None,
        directories: Optional[List[str]] = None,
        recursive: bool = True
    ):
        self.bus = bus
        self.kafka = kafka
        self.directories: List[str] = list(directories or [])
        self.recursive = recursive
        self._observer: Optional[Observer] = None
        self._handler = _WatchdogHandler(bus, kafka, self.directories)

    def add_directory(self, path: str) -> bool:
        if not os.path.isdir(path):
            return False
        self.directories.append(path)
        self._handler.watched_dirs.add(path)
        if self._observer:
            self._observer.schedule(self._handler, path, recursive=self.recursive)
        return True

    def remove_directory(self, path: str) -> bool:
        if path in self.directories:
            self.directories.remove(path)
            self._handler.watched_dirs.discard(path)
            # Watchdog doesn't support unscheduling individual watches easily; restart observer
            if self._observer:
                self.stop()
                self.start()
            return True
        return False

    def start(self):
        if self._observer:
            return
        self._observer = Observer()
        for d in self.directories:
            if os.path.isdir(d):
                self._observer.schedule(self._handler, d, recursive=self.recursive)
        self._observer.start()

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None


# ── Convenience factory for SAHIIX AGI ───────────────────────────────────────

class IngestionManager:
    """Manages all real-time ingestion components."""

    def __init__(self, bus: Optional[EventBus] = None, kafka_config: Optional[dict] = None):
        self.bus = bus or EventBus()
        self.kafka = _KafkaPublisher(**kafka_config) if kafka_config else None
        self.rss = RSSFeedMonitor(self.bus, self.kafka)
        self.poller = APIPoller(self.bus, self.kafka)
        self.watcher = FileWatcher(self.bus, self.kafka)

    async def start(self):
        await self.rss.start()
        await self.poller.start()
        self.watcher.start()

    async def stop(self):
        await self.rss.stop()
        await self.poller.stop()
        self.watcher.stop()
        if self.kafka:
            await self.kafka.close()

    def get_status(self) -> dict:
        return {
            "rss_feeds": list(self.rss.feeds.keys()),
            "api_pollers": list(self.poller.pollers.keys()),
            "watched_dirs": list(self.watcher.directories),
            "kafka": _KAFKA_AVAILABLE and self.kafka is not None,
        }
