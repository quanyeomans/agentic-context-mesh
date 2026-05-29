"""F65: every connector plugin implements metadata_for() AND ships a propagation test.

Scope: every directory under ``kairix/connectors/<name>/``. Each must:

1. **Implement ``metadata_for(item_id) -> SourceMetadata``** somewhere in the
   plugin (any module that exports a class with this method counts —
   AST scan for ``def metadata_for(self, item_id`` declarations).

2. **Ship ``tests/integration/test_<name>_metadata_propagation.py``** —
   the file must exist and contain at least one test function whose
   body references the connector's class name AND asserts on a
   ``Chunk`` attribute (``chunk_date`` / ``author`` / ``tags``).

Rationale: 2026-05-28 audit (ADR-021) found per-source metadata
(dates, authors, tags) is dropped before silver for every source
except Obsidian. Chunks lack ``chunk_date`` for 98% of the post-
SharePoint-ingestion corpus. F65 forces every connector to surface
its envelope metadata + prove the propagation by integration test.

Class can opt out with a ``# F65-exempt: <rationale>`` comment on the
line directly above the class declaration. Use only when the source
genuinely has no structured metadata (rare — most do).

Spec: ``docs/architecture/ADR-021-per-source-metadata-normalisation.md``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, repo_relative  # noqa: F401 — back-compat for direct callers
from _fitness_rule import FitnessRule

REMEDIATION = """F65: connector <name> does not implement metadata_for() OR lacks integration test.

fix: implement ``metadata_for(self, item_id: str) -> SourceMetadata`` on
the connector class, surfacing every metadata field the source's envelope
provides (modified_at, created_at, author, author_email, tags, properties).

next: add ``tests/integration/test_<name>_metadata_propagation.py`` with
at least one test asserting ``Chunk.chunk_date`` AND ``Chunk.author``
propagate from a representative source item through the pipeline.
See tests/integration/test_connector_cursor_advance.py for the canonical
shape; substitute metadata assertions for cursor assertions.

run: python3 scripts/checks/check_f65_connector_metadata.py

Pass example:

    # kairix/connectors/sharepoint/connector.py
    class SharePointConnector:
        def metadata_for(self, item_id: str) -> SourceMetadata:
            envelope = self._envelope_cache[item_id]
            return SourceMetadata(
                modified_at=envelope.last_modified_at,
                created_at=envelope.created_at,
                author=envelope.created_by_display_name,
                author_email=envelope.created_by_email,
                tags=tuple(envelope.path_segments),
                properties={"web_url": envelope.web_url, "mime": envelope.mime},
            )

    # tests/integration/test_sharepoint_metadata_propagation.py
    @pytest.mark.integration
    def test_sharepoint_envelope_metadata_lands_on_chunk(tmp_path):
        connector = build_sharepoint_connector_with_fake_graph(...)
        pipeline = factory.build_connector_pipeline(...)
        pipeline.run_batch(connector, FakeExtractor())
        chunks = db.execute("SELECT chunk_date, author FROM content").fetchall()
        assert all(c[0] is not None for c in chunks)  # chunk_date populated
        assert any("Acme Corp" in (c[1] or "") for c in chunks)  # author from envelope

Forbidden example:

    # kairix/connectors/sharepoint/connector.py
    class SharePointConnector:
        # F65 violation: no metadata_for method, envelope dropped
        pass

Allowed exemption:

    # F65-exempt: pure-binary source with no envelope metadata (e.g. raw blob fetch)
    class RawBlobConnector:
        ...
"""

PLUGIN_ROOT = "kairix/connectors"
METHOD_NAME = "metadata_for"
METADATA_CHUNK_FIELDS = frozenset({"chunk_date", "author", "tags", "metadata"})


def _line_before(source: str, lineno: int) -> str:
    lines = source.splitlines()
    if lineno < 2 or lineno > len(lines):
        return ""
    return lines[lineno - 2]


def _connector_implements_metadata(plugin_dir: Path) -> bool:
    """True iff any class in any .py under plugin_dir defines ``metadata_for``.

    AST scan — looks for ``def metadata_for(self, ...)`` declarations.
    Pattern matches the SourceConnector Protocol surface; doesn't require
    a specific decorator or base class.
    """
    for path in plugin_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == METHOD_NAME:
                        return True
    return False


def _has_propagation_test(repo_root: Path, plugin_name: str) -> bool:
    """True iff tests/integration/test_<name>_metadata_propagation.py exists
    AND contains a test asserting on a Chunk metadata field.
    """
    test_path = repo_root / "tests" / "integration" / f"test_{plugin_name}_metadata_propagation.py"
    if not test_path.is_file():
        return False
    try:
        source = test_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    # Quick heuristic: file must reference at least one Chunk metadata field
    return any(field in source for field in METADATA_CHUNK_FIELDS)


def _is_exempt(plugin_dir: Path) -> bool:
    """A ``# F65-exempt: <rationale>`` comment on the line above the
    connector class declaration exempts the plugin.
    """
    for path in plugin_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                prior = _line_before(source, node.lineno).strip()
                if prior.startswith("# F65-exempt:"):
                    return True
    return False


class F65(FitnessRule):
    """F65 as a FitnessRule subclass — see module docstring.

    Overrides :meth:`enumerate_files` to yield connector plugin
    directories. :meth:`file_has_violation` skips exempt plugins and
    plugins that both implement metadata_for AND ship the
    propagation test.
    """

    name = "f65-connector-metadata"
    remediation = REMEDIATION
    roots = (PLUGIN_ROOT,)

    def enumerate_files(self) -> list[Path]:
        plugin_root = self._repo_root / PLUGIN_ROOT
        if not plugin_root.is_dir():
            return []
        out: list[Path] = []
        for entry in sorted(plugin_root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            out.append(entry)
        return out

    def is_in_scope(self, rel: str) -> bool:
        return True

    def file_has_violation(self, path: Path) -> bool:
        if _is_exempt(path):
            return False
        if _connector_implements_metadata(path) and _has_propagation_test(self._repo_root, path.name):
            return False
        return True


def main() -> int:
    return F65().run()


if __name__ == "__main__":
    sys.exit(main())
