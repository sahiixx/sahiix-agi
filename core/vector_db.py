"""Async Qdrant vector database client for semantic search."""
import asyncio
import os
from typing import Any, Dict, List, Optional

try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
except Exception as _e:  # pragma: no cover
    AsyncQdrantClient = None
    Distance = None
    VectorParams = None
    PointStruct = None


class QdrantVectorDB:
    """Async Qdrant wrapper for upsert, search, delete, and collection management."""

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = url or os.environ.get("QDRANT_URL", "http://localhost:6333")
        self.api_key = api_key
        self._client: Optional[Any] = None
        self._lock = asyncio.Lock()

    async def connect(self):
        if AsyncQdrantClient is None:
            raise RuntimeError("qdrant-client[async] is not installed")
        async with self._lock:
            if self._client is None:
                kwargs = {"url": self.url}
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                self._client = AsyncQdrantClient(**kwargs)
        return self

    async def disconnect(self):
        async with self._lock:
            if self._client:
                await self._client.close()
                self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("QdrantVectorDB not connected. Call connect() first.")
        return self._client

    async def create_collection(self, name: str, dim: int, distance: str = "Cosine") -> bool:
        try:
            exists = await self.client.collection_exists(name)
            if exists:
                return True
            distance_map = {
                "Cosine": Distance.COSINE,
                "Euclidean": Distance.EUCLID,
                "Dot": Distance.DOT,
            }
            d = distance_map.get(distance, Distance.COSINE)
            await self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=d),
            )
            return True
        except Exception:
            return False

    async def upsert(self, collection: str, points: List[Any]) -> bool:
        try:
            structs = []
            for p in points:
                if isinstance(p, (tuple, list)):
                    if len(p) == 3:
                        _id, vector, payload = p
                    elif len(p) == 2:
                        _id, vector = p
                        payload = {}
                    else:
                        raise ValueError(f"Tuple/list must have 2 or 3 elements, got {len(p)}")
                elif isinstance(p, dict):
                    _id = p["id"]
                    vector = p["vector"]
                    payload = p.get("payload", {})
                else:
                    raise ValueError(f"Unsupported point type: {type(p)}")
                # Ensure ID is valid for Qdrant (UUID or unsigned int)
                if isinstance(_id, str) and "-" not in _id:
                    import uuid
                    _id = str(uuid.uuid5(uuid.NAMESPACE_OID, _id))
                structs.append(
                    PointStruct(
                        id=_id,
                        vector=vector,
                        payload=payload,
                    )
                )
            await self.client.upsert(collection_name=collection, points=structs, wait=True)
            return True
        except Exception:
            return False

    async def search(self, collection: str, vector: List[float], limit: int = 10, filter: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Semantic search via Qdrant query_points (v1.17+ API)."""
        try:
            resp = await self.client.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
                query_filter=filter,
                with_payload=True,
            )
            return [
                {
                    "id": r.id,
                    "score": r.score,
                    "payload": r.payload,
                }
                for r in resp.points
            ]
        except Exception:
            return []

    async def delete(self, collection: str, ids: List[Any]) -> bool:
        try:
            await self.client.delete(collection_name=collection, points_selector=ids)
            return True
        except Exception:
            return False
