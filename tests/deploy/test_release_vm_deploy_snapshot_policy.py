from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alpha_vm_deploy_requires_snapshot() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release-vm-deploy.yml").read_text()

    assert "skip-snapshot: 'true'" not in workflow
    assert "skip-snapshot: true" not in workflow
    assert "skip-snapshot: 'false'" in workflow


def test_snapshot_docs_do_not_describe_kairix_as_snapshot_skipped() -> None:
    docs = [
        REPO_ROOT / "docs/architecture/ADR-017-deployment-architecture.md",
        REPO_ROOT / "docs/architecture/ENGINEERING.md",
        REPO_ROOT / "docs/superpowers/plans/2026-07-22-kairix-operational-resilience-sprint.md",
    ]

    for doc in docs:
        text = doc.read_text()
        assert "skip-snapshot: 'true'" not in text
        assert "snapshot is skipped" not in text
        assert "snapshots should be mandatory" not in text
