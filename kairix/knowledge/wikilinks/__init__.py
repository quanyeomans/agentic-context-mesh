"""Wikilink utilities for kairix."""

from __future__ import annotations

import re

# Canonical wikilink regex — excludes anchor links (#) for safety.
# Captures the link target from [[target]] or [[target|display]].
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?\]\]")
