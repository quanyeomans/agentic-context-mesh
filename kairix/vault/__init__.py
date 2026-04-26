"""Backwards compatibility shim — kairix.vault moved to kairix.store.

This module re-exports everything from kairix.store so existing imports
continue to work. Will be removed in a future release.
"""
import warnings
warnings.warn(
    "kairix.vault is deprecated, use kairix.store instead",
    DeprecationWarning,
    stacklevel=2,
)
from kairix.store import *  # noqa: F401,F403
