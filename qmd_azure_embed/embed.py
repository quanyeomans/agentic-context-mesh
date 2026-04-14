"""Backwards-compatibility shim — imports from kairix.embed.embed."""
from kairix.embed.embed import build_hash_seq, encode_vector

__all__ = ["encode_vector", "build_hash_seq"]
