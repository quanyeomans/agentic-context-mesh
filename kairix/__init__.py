"""kairix — contextual intelligence layer for QMD + Obsidian agent stacks."""

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("kairix")
except Exception:
    __version__ = "unknown"
