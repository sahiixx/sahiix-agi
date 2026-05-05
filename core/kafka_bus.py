"""Async Kafka event bus using aiokafka."""
import asyncio
import json
import os
from typing import Any, Dict, Optional, Callable, List

try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
except Exception as _e:  # pragma: no cover
    AIOKafkaProducer = None
    AIOKafkaConsumer = None


class KafkaEventBus:
    """Async JSON event bus built on aiokafka with consumer groups."""

    def __init__(self, bootstrap_servers: Optional[str] = None):
        self.bootstrap_servers = bootstrap_servers or os.environ.get("KAFKA_URL", "localhost:9092")
        self._producer: Optional[Any] = None
        self._consumers: List[Any] = []
        self._lock = asyncio.Lock()

    async def start(self):
        if AIOKafkaProducer is None:
            raise RuntimeError("aiokafka is not installed")
        async with self._lock:
            if self._producer is None:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                )
                await self._producer.start()

    async def stop(self):
        async with self._lock:
            if self._producer:
                await self._producer.stop()
                self._producer = None
        for consumer in self._consumers:
            await consumer.stop()
        self._consumers.clear()

    async def publish(self, topic: str, event: Dict[str, Any], key: Optional[str] = None) -> bool:
        if self._producer is None:
            raise RuntimeError("KafkaEventBus not started. Call start() first.")
        try:
            await self._producer.send_and_wait(topic, value=event, key=key)
            return True
        except Exception:
            return False

    async def subscribe(
        self,
        topics: List[str],
        callback: Callable[[str, Dict[str, Any]], None],
        group_id: Optional[str] = None,
    ) -> Any:
        if AIOKafkaConsumer is None:
            raise RuntimeError("aiokafka is not installed")
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=group_id or "sahiix-agi-default",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
        )
        await consumer.start()
        self._consumers.append(consumer)

        async def _listen():
            try:
                async for msg in consumer:
                    try:
                        callback(msg.topic, msg.value)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass
            finally:
                await consumer.stop()
                if consumer in self._consumers:
                    self._consumers.remove(consumer)

        task = asyncio.create_task(_listen())
        return consumer, task

    async def create_consumer_group(self, group_id: str, topics: List[str], callback: Callable[[str, Dict[str, Any]], None]):
        """Convenience wrapper to create a named consumer group."""
        return await self.subscribe(topics=topics, callback=callback, group_id=group_id)
