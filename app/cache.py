import json
import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.embeddings import cosine_similarity


@dataclass
class CacheLookupResult:
    key: str
    response: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    similarity: float


class SemanticCache:
    def __init__(self, redis: Redis, threshold: float, ttl_seconds: int) -> None:
        self.redis = redis
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.index_key = "semantic-cache:index"

    async def lookup(self, embedding: list[float]) -> CacheLookupResult | None:
        try:
            keys = await self.redis.smembers(self.index_key)
        except RedisError:
            return None
        best: CacheLookupResult | None = None
        for raw_key in keys:
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            try:
                data = await self.redis.hgetall(key)
            except RedisError:
                continue
            if not data:
                try:
                    await self.redis.srem(self.index_key, key)
                except RedisError:
                    pass
                continue
            decoded = self._decode_hash(data)
            similarity = cosine_similarity(embedding, json.loads(decoded["embedding"]))
            if similarity < self.threshold:
                continue
            candidate = CacheLookupResult(
                key=key,
                response=decoded["response"],
                provider=decoded["provider"],
                model=decoded["model"],
                prompt_tokens=int(decoded["prompt_tokens"]),
                completion_tokens=int(decoded["completion_tokens"]),
                similarity=similarity,
            )
            if best is None or candidate.similarity > best.similarity:
                best = candidate
        return best

    async def store(
        self,
        embedding: list[float],
        response: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> str:
        key = f"semantic-cache:{uuid.uuid4().hex}"
        try:
            await self.redis.hset(
                key,
                mapping={
                    "embedding": json.dumps(embedding),
                    "response": response,
                    "provider": provider,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "created_at": str(int(time.time())),
                },
            )
            await self.redis.expire(key, self.ttl_seconds)
            await self.redis.sadd(self.index_key, key)
        except RedisError:
            return ""
        return key

    def _decode_hash(self, data: dict[bytes, bytes]) -> dict[str, str]:
        return {
            key.decode("utf-8") if isinstance(key, bytes) else key: value.decode("utf-8")
            if isinstance(value, bytes)
            else value
            for key, value in data.items()
        }
