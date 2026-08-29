import hashlib
import math

import numpy as np


class HashEmbeddingProvider:
    """Deterministic local embeddings for cache similarity without an external API."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = float(np.linalg.norm(vector))
        if math.isclose(norm, 0.0):
            return vector.tolist()
        return (vector / norm).tolist()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_arr = np.array(left, dtype=np.float32)
    right_arr = np.array(right, dtype=np.float32)
    denom = float(np.linalg.norm(left_arr) * np.linalg.norm(right_arr))
    if math.isclose(denom, 0.0):
        return 0.0
    return float(np.dot(left_arr, right_arr) / denom)

