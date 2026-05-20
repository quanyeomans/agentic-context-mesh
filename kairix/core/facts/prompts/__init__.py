"""Prompt template payloads shipped as package data.

Templates here are plain ``.txt`` files loaded via
``importlib.resources`` — they live next to the Python module that
consumes them so a ``pip install kairix`` always lands them on disk
alongside the code that needs them.
"""
