"""
Shared QMD subprocess wrapper and binary discovery.

This module provides:
  - get_qmd_binary(): locate the qmd CLI executable
  - run_qmd(): low-level subprocess runner (used by search/bm25.py)

Failure modes: returns empty values / raises only FileNotFoundError on
binary discovery. Subprocess errors are caught by callers.

Never write credentials here — this module has no auth dependencies.
"""

import os
import shutil
from pathlib import Path

# Primary QMD binary paths in search order
_QMD_SEARCH_PATHS: list[str] = [
    "/data/workspace/.tools/qmd/node_modules/.bin/qmd",
    "/data/workspace/.tools/qmd/.bin/qmd",
    "/usr/local/bin/qmd",
    "/usr/bin/qmd",
]

_QMD_ENV_VAR = "QMD_BINARY_PATH"


def get_qmd_binary() -> str:
    """
    Locate the qmd CLI binary.

    Search order:
      1. QMD_BINARY_PATH environment variable
      2. /data/workspace/.tools/qmd/node_modules/.bin/qmd  (standard QMD npm install)
      3. Other known paths
      4. PATH (shutil.which)

    Returns the absolute path as a string.
    Raises FileNotFoundError if qmd is not found anywhere.
    """
    # Explicit override
    env_path = os.environ.get(_QMD_ENV_VAR)
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return str(p)

    # Known install locations
    for candidate in _QMD_SEARCH_PATHS:
        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)

    # Fall back to PATH
    found = shutil.which("qmd")
    if found:
        return found

    raise FileNotFoundError(
        "qmd binary not found. Expected at "
        f"{_QMD_SEARCH_PATHS[0]} or on PATH. "
        f"Set {_QMD_ENV_VAR}=/path/to/qmd to override."
    )
