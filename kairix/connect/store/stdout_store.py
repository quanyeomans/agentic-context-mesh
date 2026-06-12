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
from kairix.connect.store.leaves import leaf_pairs
from kairix.secrets.naming import Scope, canonical_env_var

_BACKEND_NAME = "stdout"


class StdoutTokenStore:
    """Write captured tokens as TSV lines to a stream (default ``sys.stdout``).

    Leaves are derived dynamically from the supplied
    :class:`ClientCredentials` + :class:`CapturedTokens` dataclasses
    via :func:`kairix.connect.store.leaves.leaf_pairs` — empty-string
    fields are skipped (so Slack's empty ``refresh_token`` and
    Google's empty ``bot_token`` never appear in the output).
    """

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
        for leaf, value in leaf_pairs(client, tokens, service_area=area):
            env_name = canonical_env_var(scope, area, instance, leaf)
            self._stream.write(f"{env_name}\t{value}\n")
            canonical.append(env_name)
        self._stream.flush()
        return WriteReport(
            canonical_names=tuple(canonical),
            backend=_BACKEND_NAME,
            target="<stdout>",
        )


# Protocol conformance smoke check.
_PROTOCOL_CHECK: TokenStore = StdoutTokenStore()


__all__ = ["StdoutTokenStore"]
