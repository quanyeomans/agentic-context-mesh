"""Deterministic fake embeddings for tests — never calls Azure."""
import random


def fake_embedding(dim: int = 1536, seed: int = 0) -> list[float]:
    """Return a unit-sphere vector. Same seed → same result every time."""
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    mag = sum(x * x for x in vec) ** 0.5
    return [x / mag for x in vec]


def fake_embedding_bytes(dim: int = 1536, seed: int = 0) -> bytes:
    import struct
    vec = fake_embedding(dim=dim, seed=seed)
    return struct.pack(f"{dim}f", *vec)
