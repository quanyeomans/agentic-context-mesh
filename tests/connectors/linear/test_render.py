"""Unit tests for :mod:`kairix.connectors.linear.render`.

Pins the per-entity Markdown renderers (spec §5): one test per entity
kind plus the dispatch guard. Each test asserts on concrete output
content so a renderer that drops a field (or the dispatch table losing a
kind) fails.

F8 carries ``@pytest.mark.unit``. No network, no fakes — pure functions.
"""

from __future__ import annotations

import pytest

from kairix.connectors.linear.render import ENTITY_KINDS, render

pytestmark = pytest.mark.unit


def test_render_issue_emits_identifier_title_fields_and_body() -> None:
    """Issue render: H1 ``# <identifier> <title>`` + field block + description."""
    node = {
        "identifier": "ENG-42",
        "title": "Fix the cursor drift",
        "description": "The cursor advanced on a partial drain.",
        "url": "https://linear.app/team/issue/ENG-42",
        "state": {"name": "In Progress"},
        "assignee": {"displayName": "agent-alpha"},
        "team": {"name": "Engineering"},
        "project": {"name": "Reliability"},
        "labels": {"nodes": [{"name": "bug"}, {"name": "p1"}]},
    }
    md = render("issue", node)
    assert md.startswith("# ENG-42 Fix the cursor drift")
    assert "**State**: In Progress" in md
    assert "**Assignee**: agent-alpha" in md
    assert "**Team**: Engineering" in md
    assert "**Project**: Reliability" in md
    assert "**Labels**: bug, p1" in md
    assert "The cursor advanced on a partial drain." in md
    assert md.endswith("\n")


def test_render_project_emits_name_status_and_milestones() -> None:
    """Project render: H1 name + status/lead/target block + description + milestones."""
    node = {
        "name": "Roadmap recall",
        "description": "Make the roadmap searchable.",
        "status": {"name": "Started"},
        "lead": {"displayName": "agent-beta"},
        "targetDate": "2026-09-01",
        "projectMilestones": {"nodes": [{"name": "MVP"}, {"name": "GA"}]},
    }
    md = render("project", node)
    assert md.startswith("# Roadmap recall")
    assert "**Status**: Started" in md
    assert "**Lead**: agent-beta" in md
    assert "**Target date**: 2026-09-01" in md
    assert "## Milestones" in md
    assert "MVP, GA" in md


def test_render_document_emits_title_and_content() -> None:
    """Document render: H1 title + content body."""
    node = {
        "title": "Design doc — temporal boost",
        "content": "Temporal boost weights recency.",
        "url": "https://linear.app/team/document/abc",
        "project": {"name": "Search"},
    }
    md = render("document", node)
    assert md.startswith("# Design doc — temporal boost")
    assert "**Project**: Search" in md
    assert "Temporal boost weights recency." in md


def test_render_initiative_emits_name_and_member_projects() -> None:
    """Initiative render: H1 name + overview + member projects."""
    node = {
        "name": "FY26 platform",
        "description": "The platform initiative.",
        "status": "Active",
        "projects": {"nodes": [{"name": "Search"}, {"name": "Ingest"}]},
    }
    md = render("initiative", node)
    assert md.startswith("# FY26 platform")
    assert "The platform initiative." in md
    assert "## Projects" in md
    assert "Search, Ingest" in md


def test_render_project_update_emits_narrative_health_and_attribution() -> None:
    """Project update render: narrative + health + date, attributed to its project."""
    node = {
        "body": "On track for the GA milestone.",
        "health": "onTrack",
        "createdAt": "2026-06-01T12:00:00.000Z",
        "project": {"name": "Roadmap recall"},
    }
    md = render("projectUpdate", node)
    assert "Roadmap recall" in md
    assert "**Health**: onTrack" in md
    assert "On track for the GA milestone." in md


def test_render_tolerates_sparse_node() -> None:
    """A near-empty node still renders an H1 fallback without raising."""
    md = render("issue", {})
    assert md.startswith("# (untitled issue)")


def test_render_rejects_unknown_kind() -> None:
    """Dispatch guard: an unknown entity kind raises ValueError.

    Pins the dispatch table — dropping a kind (or mistyping a prefix)
    surfaces loudly rather than silently rendering nothing.
    """
    with pytest.raises(ValueError, match="unknown entity kind"):
        render("comment", {"id": "c-1"})


def test_entity_kinds_cover_all_five() -> None:
    """ENTITY_KINDS is exactly the five MVP entity kinds (spec §1 / §5)."""
    assert ENTITY_KINDS == frozenset({"issue", "project", "document", "initiative", "projectUpdate"})
