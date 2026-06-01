"""Shared leaf-derivation helper for every :class:`TokenStore` backend.

Per ADR-032 §"Phase 2 — TokenStore widening": the four hardcoded leaves
(``client-id``, ``client-secret``, ``refresh-token``, ``access-token``)
were Google-specific. Slack writes a different set
(``client-id``, ``client-secret``, ``bot-token``, optional
``app-token``); GitHub App will write another set
(``app-id``, ``app-private-key``, ``installation-id``).

This helper derives the leaf list from the :class:`CapturedTokens`
dataclass + :class:`ClientCredentials` dataclass at write time so the
stores stay one-shape: walk the fields, emit one
``(canonical-leaf-name, value)`` pair per non-empty string field, skip
non-string and empty-string fields. Backwards-compat: Google's
``CapturedTokens(refresh_token, access_token, token_uri,
expires_in=None, bot_token="", app_token="")`` still emits exactly
``(client-id, client-secret, refresh-token, access-token)`` because the
Slack-only fields default to empty strings.

Constants live here because all three stores reference them — F17
(no string literal ≥10 chars duplicated ≥3 times) requires hoisting.
"""

from __future__ import annotations

from dataclasses import fields

from kairix.connect.protocols import CapturedTokens, ClientCredentials

# Hoisted F21 fragments — every store's "unknown attribute" error
# surface shares the same shape. Hoisting also keeps F17 happy.
_BUG_PREFIX = "kairix connect: unknown attribute"

# Fields that are NOT secret leaves — these carry metadata
# (``token_uri``, ``expires_in``) that the OAuth-flow caller uses to
# round-trip into the refresh layer but the store should never write to
# KV under a canonical leaf name. Hoisted so the three stores share one
# allowlist (F17).
_NON_LEAF_FIELDS: frozenset[str] = frozenset(
    {
        "token_uri",
        "expires_in",
    },
)

# Field name → canonical leaf-name mapping. Each dataclass field that
# carries a writable secret value has one entry; the value is the
# hyphenated leaf the store writes under
# ``canonical_secret_name(..., leaf=<this>)``. Keys MUST match the
# field names on :class:`ClientCredentials` + :class:`CapturedTokens`.
_FIELD_TO_LEAF: dict[str, str] = {
    "client_id": "client-id",
    "client_secret": "client-secret",  # pragma: allowlist secret — leaf slot name, not a value
    "refresh_token": "refresh-token",
    "access_token": "access-token",
    "bot_token": "bot-token",
    "app_token": "app-token",
}


def leaf_pairs(
    client: ClientCredentials,
    tokens: CapturedTokens,
) -> tuple[tuple[str, str], ...]:
    """Return the ``(canonical-leaf, value)`` pairs to write to KV.

    Walks the public dataclass fields of ``client`` and ``tokens`` in
    declaration order. For each field that:

      1. Has a non-empty string value, AND
      2. Maps to a known canonical leaf in :data:`_FIELD_TO_LEAF`,

    emits one tuple ``(canonical-leaf-name, value)``. Empty strings,
    ``None``, non-string values, and metadata fields
    (:data:`_NON_LEAF_FIELDS`) are skipped — so Slack's empty
    ``refresh_token`` and Google's empty ``bot_token`` never appear in
    the output.

    The emission order is: client_id, client_secret first (operator
    identity material before token material), then tokens in field
    declaration order. This pins the operator-facing
    ``WriteReport.canonical_names`` tuple stably across runs.
    """
    pairs: list[tuple[str, str]] = []
    for source in (client, tokens):
        for field in fields(source):
            name = field.name
            if name in _NON_LEAF_FIELDS:
                continue
            leaf = _FIELD_TO_LEAF.get(name)
            if leaf is None:
                continue
            value = getattr(source, name)
            if not isinstance(value, str) or value == "":
                continue
            pairs.append((leaf, value))
    return tuple(pairs)


def unknown_attribute_error(attr: str) -> KeyError:
    """Build the ``KeyError`` every store raises for an unknown attribute.

    Hoisted so the three stores share one error surface (F17). Kept on
    the store-internal API surface, not the public Protocol — callers
    should never trigger this branch in practice.
    """
    return KeyError(
        f"{_BUG_PREFIX} {attr!r}. "
        f"fix: this is a kairix bug — please file an issue. "
        f"next: see kairix/connect/store/leaves.py::_FIELD_TO_LEAF for the canonical mapping. "
        f"run: kairix connect <service> --client-secret-path <path>",
    )


__all__ = ["leaf_pairs", "unknown_attribute_error"]
