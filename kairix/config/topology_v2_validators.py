"""Topology v2 cross-reference validators (Wave D §2).

Five referential-integrity rules per ADR v2 §"Wave D":

1. ``cc_pairs.*.connector`` references a declared connector id.
2. ``cc_pairs.*.credential`` references a declared credential id (when set).
3. ``collections.*.sources.*.cc_pair`` references a declared cc_pair id.
4. ``scope_profiles.*.entries.*.collection_name`` references a declared
   collection name.
5. ``skills.*.task_collections.*.sources.*.cc_pair`` references a declared
   cc_pair id.

Each failure renders as an F21-affordance string:

    ``<rule>: <ref> not declared. fix: add ... or remove the reference. next: run kairix config validate``

so the operator (or an agent reading the diagnostic) gets the correction
action, not just the diagnosis.

The validator is a pure function — it takes a parsed
:class:`~kairix.config.topology_v2.TopologyV2Config` and returns a
tuple of :class:`ValidationFailure` records. Callers decide whether to
log, render, or exit non-zero. The CLI surface in
:mod:`kairix.core.search.config_validator` chains this validator after
the legacy ``validate_config`` so a single ``kairix config validate``
run reports both legacy errors and Wave D referential errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kairix.config.topology_v2 import TopologyV2Config

# F17 — fix-instruction prefix duplicated across all 5 validators.
_FIX_OR_REMOVE_PREFIX = "fix: declare the missing entry or remove the reference. next: run kairix config validate"

RuleKey = Literal[
    "cc_pair_connector_missing",
    "cc_pair_credential_missing",
    "collection_source_cc_pair_missing",
    "scope_profile_entry_collection_missing",
    "skill_source_cc_pair_missing",
]


@dataclass(frozen=True)
class ValidationFailure:
    """One referential-integrity failure — frozen, hashable, sortable.

    Carries the rule key, an operator-facing message, and a stable
    location path that points at the offending entry in the source YAML
    (e.g. ``cc_pairs[0].connector``). The CLI renders ``message``; the
    JSON envelope serialises all three fields.
    """

    rule: RuleKey
    location: str
    message: str


def _render_failure(rule: RuleKey, location: str, missing: str, target: str) -> ValidationFailure:
    """Build a :class:`ValidationFailure` with an F21-compliant message."""
    return ValidationFailure(
        rule=rule,
        location=location,
        message=(f"{rule}: {target}={missing!r} at {location} is not declared in the config. {_FIX_OR_REMOVE_PREFIX}"),
    )


def _validate_cc_pair_refs(config: TopologyV2Config) -> list[ValidationFailure]:
    """Rules 1 + 2 — cc_pair.connector + cc_pair.credential references."""
    failures: list[ValidationFailure] = []
    connector_ids = {c.id for c in config.connectors}
    credential_ids = {c.id for c in config.credentials}
    for i, pair in enumerate(config.cc_pairs):
        if pair.connector not in connector_ids:
            failures.append(
                _render_failure(
                    rule="cc_pair_connector_missing",
                    location=f"cc_pairs[{i}].connector",
                    missing=pair.connector,
                    target="connector",
                )
            )
        if pair.credential is not None and pair.credential not in credential_ids:
            failures.append(
                _render_failure(
                    rule="cc_pair_credential_missing",
                    location=f"cc_pairs[{i}].credential",
                    missing=pair.credential,
                    target="credential",
                )
            )
    return failures


def _validate_collection_source_refs(config: TopologyV2Config) -> list[ValidationFailure]:
    """Rule 3 — collections.*.sources.*.cc_pair references a declared cc_pair id."""
    failures: list[ValidationFailure] = []
    cc_pair_ids = {p.id for p in config.cc_pairs}
    for i, collection in enumerate(config.collections):
        for j, source in enumerate(collection.sources):
            if source.cc_pair not in cc_pair_ids:
                failures.append(
                    _render_failure(
                        rule="collection_source_cc_pair_missing",
                        location=f"collections[{i}].sources[{j}].cc_pair",
                        missing=source.cc_pair,
                        target="cc_pair",
                    )
                )
    return failures


def _validate_scope_profile_collection_refs(config: TopologyV2Config) -> list[ValidationFailure]:
    """Rule 4 — scope_profiles.*.entries.*.collection_name references a declared collection name."""
    failures: list[ValidationFailure] = []
    collection_names = {c.name for c in config.collections}
    for i, profile in enumerate(config.scope_profiles):
        for j, entry in enumerate(profile.entries):
            if entry.collection_name not in collection_names:
                failures.append(
                    _render_failure(
                        rule="scope_profile_entry_collection_missing",
                        location=f"scope_profiles[{i}].entries[{j}].collection_name",
                        missing=entry.collection_name,
                        target="collection",
                    )
                )
    return failures


def _validate_skill_source_refs(config: TopologyV2Config) -> list[ValidationFailure]:
    """Rule 5 — skills.*.task_collections.*.sources.*.cc_pair references a declared cc_pair id."""
    failures: list[ValidationFailure] = []
    cc_pair_ids = {p.id for p in config.cc_pairs}
    for i, skill in enumerate(config.skills):
        for j, task in enumerate(skill.task_collections):
            for k, source in enumerate(task.sources):
                if source.cc_pair not in cc_pair_ids:
                    failures.append(
                        _render_failure(
                            rule="skill_source_cc_pair_missing",
                            location=f"skills[{i}].task_collections[{j}].sources[{k}].cc_pair",
                            missing=source.cc_pair,
                            target="cc_pair",
                        )
                    )
    return failures


def validate_topology_v2_references(config: TopologyV2Config) -> tuple[ValidationFailure, ...]:
    """Run all 5 cross-reference checks and return the (possibly empty) failure tuple.

    Empty config → empty tuple (zero failures). The tuple is sorted
    stably by ``(rule, location)`` so test assertions stay deterministic
    across runs.
    """
    failures: list[ValidationFailure] = []
    failures.extend(_validate_cc_pair_refs(config))
    failures.extend(_validate_collection_source_refs(config))
    failures.extend(_validate_scope_profile_collection_refs(config))
    failures.extend(_validate_skill_source_refs(config))
    failures.sort(key=lambda f: (f.rule, f.location))
    return tuple(failures)
