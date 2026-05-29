"""Feature flag pattern — see docs/architecture/feature-flag-architecture.md.

Public surface:
    flag(name: str) -> bool   # cached per-process; logs first activation
    status() -> tuple[FlagStatus, ...]   # for kairix features status CLI

Future PRs add fitness functions (F51-F54) that mechanically enforce
retirement deadlines, call-site reference integrity, operator-surface
availability, and both-branch test coverage.
"""

from kairix.core.features.capability import FlagGatedCapability
from kairix.core.features.registry import REGISTRY, FeatureFlag
from kairix.core.features.resolver import FlagStatus, flag, status

__all__ = ["REGISTRY", "FeatureFlag", "FlagGatedCapability", "FlagStatus", "flag", "status"]
