"""Backwards compatibility shim — kairix.vault.health moved to kairix.store.health."""

import warnings

warnings.warn(
    "kairix.vault.health is deprecated, use kairix.store.health instead",
    DeprecationWarning,
    stacklevel=2,
)
from kairix.store.health import *  # noqa: E402, F403
from kairix.store.health import (  # noqa: E402, F401 — explicit re-exports
    StoreHealthReport,
    VaultHealthReport,
    format_health_text,
    run_store_health,
    run_vault_health,
)
