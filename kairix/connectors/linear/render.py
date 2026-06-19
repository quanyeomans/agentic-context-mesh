"""Per-entity Markdown rendering for the Linear connector (spec §5).

:func:`render` dispatches by entity ``kind`` to one of five per-entity
renderers. Linear content (issue descriptions, document bodies, project
overviews) is already Markdown at the source, so rendering is mostly a
matter of emitting an H1 title, a field block, and the body — no block
tree walk (unlike Notion). Each renderer tolerates absent / null fields
because the GraphQL response leaves most fields optional.

Chunking happens downstream in ``kairix/core/connectors/silver.py``
(F38) — these functions only produce raw Markdown text; the connector
encodes it to bytes for the :class:`RawArtefact`.

Dispatch by the ``item_id`` type prefix (issue / project / document /
initiative / projectUpdate) is the connector's job (see
``connector.py``); this module renders one already-resolved node.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# Entity kinds the connector recognises. The connector's ``item_id``
# type prefix maps 1:1 onto these. Extracted as a frozenset so the
# connector + tests share one source of truth for the valid kinds.
ENTITY_KINDS: frozenset[str] = frozenset({"issue", "project", "document", "initiative", "projectUpdate"})

# Field-label constants — extracted so the F17 duplicate-literal gate
# stays green across the five renderers (several reuse the same labels).
_LABEL_STATE = "State"
_LABEL_ASSIGNEE = "Assignee"
_LABEL_TEAM = "Team"
_LABEL_PROJECT = "Project"
_LABEL_LABELS = "Labels"
_LABEL_URL = "URL"
_LABEL_LEAD = "Lead"
_LABEL_STATUS = "Status"
_LABEL_TARGET_DATE = "Target date"
_LABEL_HEALTH = "Health"
_LABEL_DATE = "Date"

# Node field name referenced ≥3 times — extracted for the F17 gate.
_FIELD_DESCRIPTION = "description"


def render(kind: str, node: Mapping[str, Any]) -> str:
    """Render one Linear entity ``node`` to Markdown for the given ``kind``.

    Args:
        kind: One of :data:`ENTITY_KINDS`.
        node: The GraphQL node dict for the entity.

    Returns:
        Markdown text (trailing single newline).

    Raises:
        ValueError: If ``kind`` is not a recognised entity kind — a
            mis-typed item_id prefix is a bug in the dispatch table, not
            a recoverable runtime condition.
    """
    renderer = _RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(
            f"linear render: unknown entity kind {kind!r}. "
            f"fix: dispatch only the prefixes in ENTITY_KINDS "
            f"(issue / project / document / initiative / projectUpdate). "
            f"next: see kairix/connectors/linear/connector.py for the item_id prefix map."
        )
    return renderer(node)


# ---------------------------------------------------------------------------
# Per-entity renderers
# ---------------------------------------------------------------------------


def _render_issue(node: Mapping[str, Any]) -> str:
    """Issue — ``# <identifier> <title>`` + field block + description body."""
    identifier = _str(node.get("identifier"))
    title = _str(node.get("title"))
    heading = f"{identifier} {title}".strip() or "(untitled issue)"
    fields: list[tuple[str, str]] = [
        (_LABEL_STATE, _nested_name(node.get("state"))),
        (_LABEL_ASSIGNEE, _nested_display(node.get("assignee"))),
        (_LABEL_TEAM, _nested_name(node.get("team"))),
        (_LABEL_PROJECT, _nested_name(node.get("project"))),
        (_LABEL_LABELS, _join_labels(node.get("labels"))),
        (_LABEL_URL, _str(node.get("url"))),
    ]
    return _assemble(heading, fields, _str(node.get(_FIELD_DESCRIPTION)))


def _render_project(node: Mapping[str, Any]) -> str:
    """Project — ``# <name>`` + status/lead/target-date block + description + milestones."""
    heading = _str(node.get("name")) or "(untitled project)"
    fields: list[tuple[str, str]] = [
        (_LABEL_STATUS, _nested_name(node.get("status")) or _str(node.get("state"))),
        (_LABEL_LEAD, _nested_display(node.get("lead"))),
        (_LABEL_TARGET_DATE, _str(node.get("targetDate"))),
        (_LABEL_URL, _str(node.get("url"))),
    ]
    milestones = _join_node_names(node.get("projectMilestones"))
    body_parts: list[str] = []
    description = _str(node.get(_FIELD_DESCRIPTION))
    if description:
        body_parts.append(description)
    if milestones:
        body_parts.append(f"## Milestones\n\n{milestones}")
    return _assemble(heading, fields, "\n\n".join(body_parts))


def _render_document(node: Mapping[str, Any]) -> str:
    """Document — ``# <title>`` + content body (Linear Documents are Markdown)."""
    heading = _str(node.get("title")) or "(untitled document)"
    fields: list[tuple[str, str]] = [
        (_LABEL_PROJECT, _nested_name(node.get("project"))),
        (_LABEL_URL, _str(node.get("url"))),
    ]
    return _assemble(heading, fields, _str(node.get("content")))


def _render_initiative(node: Mapping[str, Any]) -> str:
    """Initiative — ``# <name>`` + overview/description + member projects."""
    heading = _str(node.get("name")) or "(untitled initiative)"
    fields: list[tuple[str, str]] = [
        (_LABEL_STATUS, _nested_name(node.get("status")) or _str(node.get("status"))),
        (_LABEL_URL, _str(node.get("url"))),
    ]
    member_projects = _join_node_names(node.get("projects"))
    body_parts: list[str] = []
    description = _str(node.get(_FIELD_DESCRIPTION))
    if description:
        body_parts.append(description)
    if member_projects:
        body_parts.append(f"## Projects\n\n{member_projects}")
    return _assemble(heading, fields, "\n\n".join(body_parts))


def _render_project_update(node: Mapping[str, Any]) -> str:
    """Project / status update — narrative + health + date, attributed to its project."""
    project_name = _nested_name(node.get("project"))
    heading = f"Update — {project_name}".strip(" —") or "Project update"
    fields: list[tuple[str, str]] = [
        (_LABEL_PROJECT, project_name),
        (_LABEL_HEALTH, _str(node.get("health"))),
        (_LABEL_DATE, _str(node.get("createdAt"))),
        (_LABEL_URL, _str(node.get("url"))),
    ]
    return _assemble(heading, fields, _str(node.get("body")))


# ---------------------------------------------------------------------------
# Shared shaping helpers
# ---------------------------------------------------------------------------


def _assemble(heading: str, fields: list[tuple[str, str]], body: str) -> str:
    """Assemble the H1 heading, the non-empty field block, and the body."""
    lines: list[str] = [f"# {heading}", ""]
    field_lines = [f"- **{label}**: {value}" for label, value in fields if value]
    if field_lines:
        lines.extend(field_lines)
        lines.append("")
    body = body.strip()
    if body:
        lines.append(body)
    return "\n".join(lines).rstrip() + "\n"


def _str(value: Any) -> str:
    """Return ``value`` as a stripped string, or empty for None / non-str."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _nested_name(value: Any) -> str:
    """Pull ``.name`` from a nested object (state / team / project / status)."""
    if isinstance(value, Mapping):
        return _str(value.get("name"))
    return ""


def _nested_display(value: Any) -> str:
    """Pull ``.displayName`` (falling back to ``.name``) from a user object."""
    if isinstance(value, Mapping):
        return _str(value.get("displayName")) or _str(value.get("name"))
    return ""


def _join_labels(value: Any) -> str:
    """Join a Linear ``labels { nodes { name } }`` connection into a string."""
    return _join_node_names(value)


def _join_node_names(value: Any) -> str:
    """Join a GraphQL ``{ nodes: [{ name } ...] }`` connection's names.

    Tolerates the bare-list shape (already-unwrapped ``nodes``) too so a
    fixture can pass either ``{"nodes": [...]}`` or ``[...]`` directly.
    """
    nodes: Any
    if isinstance(value, Mapping):
        nodes = value.get("nodes", [])
    elif isinstance(value, list):
        nodes = value
    else:
        return ""
    names: list[str] = []
    for entry in nodes:
        name = _nested_name(entry) if isinstance(entry, Mapping) else _str(entry)
        if name:
            names.append(name)
    return ", ".join(names)


# Dispatch table — built after the renderers so the references resolve.
# Keys mirror :data:`ENTITY_KINDS` exactly (a kind missing here would
# raise in :func:`render`, which the unit tests pin).
_RENDERERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "issue": _render_issue,
    "project": _render_project,
    "document": _render_document,
    "initiative": _render_initiative,
    "projectUpdate": _render_project_update,
}
