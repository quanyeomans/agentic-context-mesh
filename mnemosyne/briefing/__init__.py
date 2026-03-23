"""
mnemosyne.briefing — Session briefing synthesis.

Public API:
  generate_briefing(agent: str) -> str
      Generate a session briefing for the given agent.
      Writes to /data/mnemosyne/briefing/<agent>-latest.md and returns the content.
      Never raises — returns partial content on any failure.
"""

from mnemosyne.briefing.pipeline import generate_briefing

__all__ = ["generate_briefing"]
