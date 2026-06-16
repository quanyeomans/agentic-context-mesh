"""Skip the architecture suite when the 3.12-only ``tc_fitness`` engine is absent.

``three-cubes-fitness`` (the shared fitness engine, EPIC #499) is pinned
``python_version >= '3.12'`` in ``pyproject.toml`` — on the 3.10/3.11 CI legs it
is deliberately not installed, so the tests in this directory (which import
``tc_fitness`` at collection time, directly or via ``scripts/checks``) cannot be
collected there. They assert code *structure* (CI fan-in parity, prod/test import
boundaries), which is Python-version-independent and runs in full on the 3.12 leg,
so skipping their collection on 3.10/3.11 loses no coverage — it just stops the
``ModuleNotFoundError: No module named 'tc_fitness'`` from erroring the whole
Stage-2 leg. See the ``three-cubes-fitness ... python_version >= '3.12'`` marker
in pyproject.toml.
"""

import importlib.util

# tc_fitness absent (3.10/3.11) → ignore this directory's test collection.
collect_ignore_glob = ["*"] if importlib.util.find_spec("tc_fitness") is None else []
