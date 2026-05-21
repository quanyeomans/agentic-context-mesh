"""Tests for the ``scripts/checks/check_*.py`` architecture-fitness detectors.

Each detector file under ``scripts/checks/`` lives outside the ``kairix/``
package, so these tests pin its public surface via a ``sys.path`` insert
and import the module by its basename. The pattern mirrors
``tests/architecture/test_check_no_internal_patches.py``.
"""
