"""Backwards compatibility shim — kairix.vault.cli moved to kairix.store.cli."""
import warnings
warnings.warn(
    "kairix.vault.cli is deprecated, use kairix.store.cli instead",
    DeprecationWarning,
    stacklevel=2,
)
from kairix.store.cli import *  # noqa: F401,F403
from kairix.store.cli import main  # noqa: F401 — explicit re-export for entry point
