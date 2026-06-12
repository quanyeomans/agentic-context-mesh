"""Shared leaf-derivation helper for every :class:`TokenStore` backend.

Per ADR-032 §"Phase 2 — TokenStore widening": the four hardcoded leaves
(``client-id``, ``client-secret``, ``refresh-token``, ``access-token``)
were Google-specific. Slack writes a different set
(``client-id``, ``client-secret``, ``bot-token``, optional
``app-token``); GitHub App writes another set (``app-id``,
``app-private-key``, ``access-token``, ``installation-id``) — the App
flow repurposes the ``client_id`` / ``client_secret`` dataclass slots
for the App id + PEM key, and :data:`SERVICE_LEAF_OVERRIDES` remaps
those two fields to the leaf names the GitHub connector's credential
resolver actually reads.

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

# Per-service-area leaf-name remaps layered over :data:`_FIELD_TO_LEAF`.
# GitHub App repurposes the ``client_id`` / ``client_secret`` slots for
# the App id + PEM private key (see
# ``kairix/connect/oauth2/github_app.py``); the connector's credential
# resolver reads ``app-id`` / ``app-private-key``, so the stores must
# write those names — writing ``client-id`` / ``client-secret`` leaves
# the connector resolving ``app_id=None`` and falling into the legacy
# path that cannot mint installation tokens.
SERVICE_LEAF_OVERRIDES: dict[str, dict[str, str]] = {
    "github": {
        "client_id": "app-id",
        "client_secret": "app-private-key",  # pragma: allowlist secret — leaf slot name, not a value
    },
}


def _leaf_pair_for_field(
    source: object,
    field_name: str,
    overrides: dict[str, str],
) -> tuple[str, str] | None:
    """Return the ``(canonical-leaf, value)`` pair for one dataclass field, or None.

    Hoisted from :func:`leaf_pairs` so the inner per-field decision
    (skip-non-leaf / skip-unmapped / skip-empty / emit) collapses into
    one helper call — Sonar S3776 (cognitive complexity) refactor. A
    field is skipped (``None`` return) when:

      * The field name appears in :data:`_NON_LEAF_FIELDS` (metadata, not
        a writable secret).
      * The field name is not in :data:`_FIELD_TO_LEAF` (unknown).
      * The field's value is not a non-empty string (Slack's empty
        ``refresh_token`` and Google's empty ``bot_token`` flow through
        this path).

    ``overrides`` is the service-area remap from
    :data:`SERVICE_LEAF_OVERRIDES` (empty for services without one).
    """
    if field_name in _NON_LEAF_FIELDS:
        return None
    leaf = overrides.get(field_name) or _FIELD_TO_LEAF.get(field_name)
    if leaf is None:
        return None
    value = getattr(source, field_name)
    if not isinstance(value, str) or value == "":
        return None
    return (leaf, value)


def _meta_pair(meta_key: str, meta_value: object) -> tuple[str, str] | None:
    """Return the ``(canonical-leaf, value)`` pair for one metadata entry, or None.

    The leaf written to KV is the canonical ``kebab-case`` form;
    ``snake_case`` keys are normalised (``installation_id`` →
    ``installation-id``) and already-kebab keys (the GitHub App flow's
    ``GITHUB_INSTALLATION_ID_METADATA_KEY``) pass through unchanged.
    Empty / non-string values are skipped (matches the base-leaf
    behaviour for Slack's empty ``refresh_token``).
    """
    if not isinstance(meta_value, str) or meta_value == "":
        return None
    return (meta_key.replace("_", "-"), meta_value)


def leaf_pairs(
    client: ClientCredentials,
    tokens: CapturedTokens,
    *,
    service_area: str | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return the ``(canonical-leaf, value)`` pairs to write to KV.

    Walks the public dataclass fields of ``client`` and ``tokens`` in
    declaration order. For each field that:

      1. Has a non-empty string value, AND
      2. Maps to a known canonical leaf in :data:`_FIELD_TO_LEAF`
         (after the ``service_area`` remap in
         :data:`SERVICE_LEAF_OVERRIDES`, when one exists),

    emits one tuple ``(canonical-leaf-name, value)``. Empty strings,
    ``None``, non-string values, and metadata fields
    (:data:`_NON_LEAF_FIELDS`) are skipped — so Slack's empty
    ``refresh_token`` and Google's empty ``bot_token`` never appear in
    the output.

    ``service_area`` is the canonical-naming "area" slot the calling
    store received (``"github"``, ``"slack"``, ``"gmail"``, …). Areas
    with an entry in :data:`SERVICE_LEAF_OVERRIDES` get their repurposed
    dataclass slots written under the leaf names the matching
    connector's credential resolver reads (GitHub App: ``client_id`` →
    ``app-id``, ``client_secret`` → ``app-private-key``).

    The emission order is: client_id, client_secret first (operator
    identity material before token material), then tokens in field
    declaration order. This pins the operator-facing
    ``WriteReport.canonical_names`` tuple stably across runs.

    Per-service metadata leaves on top of the base set. The GitHub App
    flow uses this to carry ``installation-id`` (the App-install
    callback returns no refresh_token; the installation id IS the
    per-tenant identifier the connector pairs with the JWT signing key
    to mint installation access tokens on demand).
    """
    overrides = SERVICE_LEAF_OVERRIDES.get(service_area or "", {})
    pairs: list[tuple[str, str]] = []
    for source in (client, tokens):
        for field in fields(source):
            pair = _leaf_pair_for_field(source, field.name, overrides)
            if pair is not None:
                pairs.append(pair)
    for meta_key, meta_value in tokens.metadata.items():
        meta_pair = _meta_pair(meta_key, meta_value)
        if meta_pair is not None:
            pairs.append(meta_pair)
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


__all__ = ["SERVICE_LEAF_OVERRIDES", "leaf_pairs", "unknown_attribute_error"]
