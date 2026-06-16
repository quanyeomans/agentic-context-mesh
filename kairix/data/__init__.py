"""Package-data namespace for kairix bundled assets.

This package exists so the benchmark suite YAMLs under ``suites/`` ship
inside the installed wheel as package-data (declared in
``pyproject.toml`` under ``[tool.setuptools.package-data]``). Without an
``__init__.py`` here, ``[tool.setuptools.packages.find]`` would not
discover ``kairix.data`` and the suites would be excluded from the
wheel — the exact #450 packaging gap.

The heavy reference-library corpus (~50 MB, mixed-license) is NOT
bundled. It is fetched on demand by ``kairix benchmark install-corpus``
and resolved at runtime by :func:`kairix.paths.reference_library_root`.
"""
