"""Canonical credential naming for kairix.

Single rule across operators / KV providers: every kairix secret has a
canonical name derived deterministically from the four pieces of
identity the loader needs.

KV form:   ``kairix-<scope>-<area>[-<instance>]-<leaf>``
Env form:  ``KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>``
                (uppercase, hyphens become underscores)

* ``scope`` is one of ``connector``, ``provider``, ``infra`` — the
  three families of kairix-bound credentials.
* ``area`` is the connector / provider / infra component name as it
  appears in the codebase (``sharepoint``, ``m365``, ``github``,
  ``azure-openai``, ``embed``, ``llm``, ``neo4j``). Underscores in
  Python module names become hyphens in the KV form so the canonical
  name is hyphenated end-to-end.
* ``instance`` is the optional per-deployment disambiguator. Present
  when the operator runs multiple of the same area (Obsidian vault
  ``tcv`` vs ``personal``; Slack workspace ``alpha`` vs ``coach``).
  Absent for singleton areas (only one M365 tenant per kairix install).
* ``leaf`` is the specific credential (``tenant-id``, ``client-secret``,
  ``pat``, ``api-key``, ``encryption-password``).

The parser ambiguity
--------------------
``kairix-connector-obsidian-tcv-encryption-password`` and
``kairix-connector-obsidian-encryption-password`` are both valid; the
parser cannot know which token is the instance without help. The chosen
tie-break is: **leaf is the last token; everything between the area
and the last token is the instance**. This works because every leaf
in the canonical map is a single hyphen-joined identifier slot, by
convention.

Forbidden:   ``kairix-connector-obsidian-tcv-encryption-password`` →
             instance="tcv-encryption", leaf="password" (the parser
             would mis-split).

Resolution:  leaf names of more than one slot use underscore-style
             single tokens (``encryptionpassword``) OR — preferred —
             callers that want such a leaf register the instance form
             via :data:`kairix.secrets._legacy_aliases.LEGACY_ALIASES`
             so the loader doesn't need to round-trip through the
             parser at all. The canonical names emitted by
             :func:`canonical_secret_name` are always unambiguous; the
             parser is for inspection-tool round-tripping only.

In practice the codebase keeps all leaves to single-slot identifiers
and the loader never round-trips through the parser. The parser
exists so the ``kairix secrets verify`` CLI + operator tools can pretty-
print a KV listing.
"""

from __future__ import annotations

from typing import Literal

Scope = Literal["connector", "provider", "infra"]

_VALID_SCOPES: frozenset[str] = frozenset({"connector", "provider", "infra"})


def canonical_secret_name(
    scope: Scope,
    area: str,
    instance: str | None,
    leaf: str,
) -> str:
    """Return the canonical KV secret name for the given identity tuple.

    Example::

        >>> canonical_secret_name("connector", "sharepoint", None, "tenant-id")
        'kairix-connector-sharepoint-tenant-id'
        >>> canonical_secret_name("connector", "obsidian", "tcv", "encryption-password")
        'kairix-connector-obsidian-tcv-encryption-password'

    Raises ``ValueError`` for empty area / leaf or unknown scope.
    """
    _validate_parts(scope, area, leaf)
    parts = ["kairix", scope, _normalise(area)]
    if instance:
        parts.append(_normalise(instance))
    parts.append(_normalise(leaf))
    return "-".join(parts)


def canonical_env_var(
    scope: Scope,
    area: str,
    instance: str | None,
    leaf: str,
) -> str:
    """Return the canonical env-var name (KAIRIX_*) for the identity tuple.

    Equivalent to :func:`canonical_secret_name` with ``-`` replaced by
    ``_`` and uppercased.
    """
    return canonical_secret_name(scope, area, instance, leaf).replace("-", "_").upper()


def parse_canonical_name(name: str) -> tuple[Scope, str, str | None, str]:
    """Parse a canonical KV secret name back into its identity tuple.

    Inverse of :func:`canonical_secret_name`. The tie-break rule (see
    module docstring): the last token is the leaf; anything between
    the area and the last token is the instance.

    Example::

        >>> parse_canonical_name("kairix-connector-sharepoint-tenant")
        ('connector', 'sharepoint', None, 'tenant')
        >>> parse_canonical_name("kairix-connector-obsidian-tcv-pass")
        ('connector', 'obsidian', 'tcv', 'pass')

    The parser uses the LAST hyphen as the leaf boundary, so leaf
    names that contain hyphens (``tenant-id``, ``client-secret``,
    ``encryption-password``) end up partially split — the parser
    cannot tell ``kairix-connector-sharepoint-tenant-id`` apart from
    ``kairix-connector-sharepoint-tenant`` with an ``id`` instance.
    Round-trip safety is therefore conditional on the leaf being a
    single slot, or on the caller round-tripping through
    :data:`kairix.secrets._legacy_aliases.LEGACY_ALIASES` (which
    stores the well-formed identity tuple directly so the parser is
    never needed for actual resolution).

    Raises ``ValueError`` for malformed names (wrong prefix, fewer
    than four parts, unknown scope).
    """
    if not name.startswith("kairix-"):
        raise ValueError(f"Canonical name must start with 'kairix-'; got {name!r}.")
    tail = name[len("kairix-") :]
    parts = tail.split("-")
    if len(parts) < 3:
        raise ValueError(
            f"Canonical name must have at least 'scope-area-leaf'; got {name!r}.",
        )
    scope = parts[0]
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Unknown scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}.",
        )
    # Tie-break: last token is leaf; anything between area and last is instance.
    area = parts[1]
    leaf = parts[-1]
    instance_parts = parts[2:-1]
    instance = "-".join(instance_parts) if instance_parts else None
    # Cast is safe because we validated scope above.
    return scope, area, instance, leaf  # type: ignore[return-value]  # F3 rationale: scope validated against _VALID_SCOPES above; mypy can't narrow a runtime check.


def _normalise(part: str) -> str:
    """Lowercase + underscore-to-hyphen for canonical-name segments.

    Module-style identifiers (``apple_caldav``) become hyphenated
    (``apple-caldav``) so the canonical name is hyphenated end-to-end.
    """
    return part.replace("_", "-").lower()


def _validate_parts(scope: str, area: str, leaf: str) -> None:
    """Reject empty / unknown identity pieces early so the canonical
    name is always well-formed.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Unknown scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}.",
        )
    if not area:
        raise ValueError("area must be a non-empty string.")
    if not leaf:
        raise ValueError("leaf must be a non-empty string.")


__all__ = [
    "Scope",
    "canonical_env_var",
    "canonical_secret_name",
    "parse_canonical_name",
]
