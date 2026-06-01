"""Stdout token store — emits TSV ``<CANONICAL_ENV_VAR>\\t<value>`` lines.

Lets the operator pipe ``kairix connect ... --store=stdout`` into any
KV import tool they prefer (``tee``, ``op``, custom scripts). Each line
is exactly two fields separated by a tab; trailing newline included.

F15-clean: the values are written to stdout because the operator
explicitly asked for that destination (``--store=stdout``). Default
backend is the file store — operators have to opt into stdout
emission, so this isn't a leak.
"""

from __future__ import annotations

import sys
from typing import TextIO

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStore,
    WriteReport,
)
from kairix.secrets.naming import Scope, canonical_env_var

_BACKEND_NAME = "stdout"

# Same four leaves the file store writes — kept in this order so the
# TSV output is stable across backends.
_LEAVES: tuple[tuple[str, str], ...] = (
    ("client-id", "client_id"),
    ("client-secret", "client_secret"),
    ("refresh-token", "refresh_token"),
    ("access-token", "access_token"),
)


class StdoutTokenStore:
    """Write captured tokens as TSV lines to a stream (default ``sys.stdout``)."""

    def __init__(self, *, stream: TextIO | None = None) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stdout

    def store(
        self,
        *,
        scope: Scope,
        area: str,
        instance: str | None,
        tokens: CapturedTokens,
        client: ClientCredentials,
    ) -> WriteReport:
        canonical: list[str] = []
        for leaf, attr in _LEAVES:
            env_name = canonical_env_var(scope, area, instance, leaf)
            value = _resolve_value(attr, tokens, client)
            self._stream.write(f"{env_name}\t{value}\n")
            canonical.append(env_name)
        self._stream.flush()
        return WriteReport(
            canonical_names=tuple(canonical),
            backend=_BACKEND_NAME,
            target="<stdout>",
        )


def _resolve_value(attr: str, tokens: CapturedTokens, client: ClientCredentials) -> str:
    """Resolve the per-leaf value — mirrors the file_store helper."""
    if attr == "client_id":
        return client.client_id
    if attr == "client_secret":
        return client.client_secret
    if attr == "refresh_token":
        return tokens.refresh_token
    if attr == "access_token":
        return tokens.access_token
    raise KeyError(f"kairix connect: unknown attribute {attr!r}.")


# Protocol conformance smoke check.
_PROTOCOL_CHECK: TokenStore = StdoutTokenStore()


__all__ = ["StdoutTokenStore"]
