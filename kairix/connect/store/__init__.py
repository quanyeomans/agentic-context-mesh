"""Token-store backends for ``kairix connect``.

Three backends ship in Phase 1:

  * :mod:`kairix.connect.store.file_store` — writes to
    ``$KAIRIX_SECRETS_FILE`` (default
    ``~/.config/kairix/secrets/kairix.env``).
  * :mod:`kairix.connect.store.azure_kv_store` — writes via the Azure
    SDK using ``DefaultAzureCredential``.
  * :mod:`kairix.connect.store.stdout_store` — emits TSV lines suitable
    for piping into ``tee``, ``op``, or a custom KV import script.
"""

from __future__ import annotations
