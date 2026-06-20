"""Skills connector plugin — local Claude Code skills/commands/agents (Feeder 2).

Credential-less :class:`kairix.core.protocols.SourceConnector`
implementation that indexes the host's locally-installed Claude Code
skills, slash-commands, and sub-agents into the ``capabilities``
collection — the external half of the capability-recommender corpus
(design §3.4). It walks the ``~/.claude`` filesystem tree, parses each
artefact's YAML frontmatter, dedups by name preferring the higher
version, renders each to Markdown, and emits one
:class:`~kairix.core.protocols.ChangeEvent` per artefact with a
kind-prefixed ``item_id`` (``skill:…`` / ``command:…`` / ``agent:…``).

There is **no auth**: the source is the local filesystem, so there is no
secret resolution and no network. Where ``~/.claude`` is absent — the
production VM — the connector finds nothing and the corpus stays
kairix-caps-only (graceful degrade, never an error; design §3.4 / §7).

Registered via ``[project.entry-points."kairix.connectors"]`` in kairix's
``pyproject.toml`` — operators select it by listing ``skills`` in their
``connectors[]`` config, behind the ``connector_skills`` feature flag
(introduce stage, default off; see
``docs/architecture/feature-flag-architecture.md`` §3).

Default sensitivity tier is ``internal`` per design §3.4 (locally
installed dev tooling metadata is company-internal, not secret).

The plugin is mypy-strict-clean and carries ``py.typed`` per F41. The
``SourceConnector`` Protocol surface is narrow by design (F35 / F38);
chunking happens downstream in ``kairix/core/connectors/silver.py``.

See ``tests/bdd/features/connector_skills.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

from kairix.connectors.skills.connector import (
    CONNECTOR_NAME,
    CONNECTOR_SKILLS_FLAG,
    DEFAULT_PER_TICK_MAX_ITEMS,
    DEFAULT_SENSITIVITY,
    SKILLS_MARKDOWN_MIME,
    SkillsConnector,
    make_connector,
)
from kairix.connectors.skills.fs import SkillArtefact, iter_skill_artefacts

# F56 capability declaration. The skills connector satisfies the base
# SourceConnector plus PollConnector (single-container delta poll on the
# ~/.claude tree's file mtimes) and SlimConnector (id-only enumeration for
# the prune cycle). It is credential-less (local FS, no auth) so
# CredentialsConnector / OAuthConnector do not apply; EventConnector
# (inotify) and HierarchyConnector are out of scope for the MVP.
CAPABILITIES: frozenset[str] = frozenset(
    {
        "SourceConnector",
        "PollConnector",
        "SlimConnector",
    }
)

__all__ = [
    "CAPABILITIES",
    "CONNECTOR_NAME",
    "CONNECTOR_SKILLS_FLAG",
    "DEFAULT_PER_TICK_MAX_ITEMS",
    "DEFAULT_SENSITIVITY",
    "SKILLS_MARKDOWN_MIME",
    "SkillArtefact",
    "SkillsConnector",
    "iter_skill_artefacts",
    "make_connector",
]

# Plugin version (mirrors the per-plugin version convention). MVP / Spec A.
version = "1.0"
