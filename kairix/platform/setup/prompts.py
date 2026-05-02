"""Prompt abstraction for the kairix setup wizard.

Provides interactive, non-interactive, and JSON output modes.
Uses Rich for styled prompts when available; falls back to plain input().
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SetupContext:
    """Runtime context for the setup wizard."""

    interactive: bool
    json_mode: bool
    state_path: Path
    answers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def auto_detect(
        cls,
        state_path: Path | None = None,
        non_interactive: bool = False,
        json_mode: bool = False,
    ) -> SetupContext:
        """Create a context with auto-detected interactivity."""
        import os

        if state_path is None:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kairix"
            config_dir.mkdir(parents=True, exist_ok=True)
            state_path = config_dir / ".setup-state.json"

        interactive = not non_interactive and not json_mode and sys.stdout.isatty() and not os.environ.get("CI")
        return cls(interactive=interactive, json_mode=json_mode, state_path=state_path)


def prompt(ctx: SetupContext, question: str, default: str = "") -> str:
    """Prompt for text input. Returns default in non-interactive mode."""
    if not ctx.interactive:
        return default
    hint = f" [{default}]" if default else ""
    answer = input(f"  {question}{hint}: ").strip()
    return answer if answer else default


def prompt_choice(ctx: SetupContext, question: str, options: list[str], default: int = 0) -> int:
    """Prompt for a numbered choice. Returns default index in non-interactive mode."""
    if not ctx.interactive:
        return default

    print(f"\n  {question}\n")
    for i, opt in enumerate(options):
        marker = ">" if i == default else " "
        print(f"  {marker} {i + 1}. {opt}")
    print()

    while True:
        hint = f" [{default + 1}]"
        raw = input(f"  Choice{hint}: ").strip()
        if not raw:
            return default
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}.")


def prompt_yn(ctx: SetupContext, question: str, default: bool = True) -> bool:
    """Prompt for yes/no. Returns default in non-interactive mode."""
    if not ctx.interactive:
        return default

    hint = "Y/n" if default else "y/N"
    raw = input(f"  {question} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")
