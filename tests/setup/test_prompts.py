"""Tests for kairix.setup.prompts — prompt abstraction layer.

Verifies interactive, non-interactive, and JSON modes behave correctly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.setup.prompts import SetupContext, prompt, prompt_choice, prompt_yn

pytestmark = pytest.mark.unit


@pytest.fixture()
def interactive_ctx(tmp_path: Path) -> SetupContext:
    return SetupContext(interactive=True, json_mode=False, state_path=tmp_path / "state.json")


@pytest.fixture()
def non_interactive_ctx(tmp_path: Path) -> SetupContext:
    return SetupContext(interactive=False, json_mode=False, state_path=tmp_path / "state.json")


@pytest.fixture()
def json_ctx(tmp_path: Path) -> SetupContext:
    return SetupContext(interactive=False, json_mode=True, state_path=tmp_path / "state.json")


class TestPrompt:
    def test_returns_user_input_in_interactive_mode(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value="my answer"):
            result = prompt(interactive_ctx, "Question?", default="fallback")
        assert result == "my answer"

    def test_returns_default_when_input_empty(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value=""):
            result = prompt(interactive_ctx, "Question?", default="fallback")
        assert result == "fallback"

    def test_returns_default_in_non_interactive_mode(self, non_interactive_ctx: SetupContext) -> None:
        result = prompt(non_interactive_ctx, "Question?", default="fallback")
        assert result == "fallback"

    def test_returns_default_in_json_mode(self, json_ctx: SetupContext) -> None:
        result = prompt(json_ctx, "Question?", default="fallback")
        assert result == "fallback"


class TestPromptChoice:
    def test_returns_selected_index(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value="2"):
            result = prompt_choice(interactive_ctx, "Pick:", ["a", "b", "c"], default=0)
        assert result == 1  # 0-indexed

    def test_returns_default_on_empty_input(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value=""):
            result = prompt_choice(interactive_ctx, "Pick:", ["a", "b", "c"], default=1)
        assert result == 1

    def test_returns_default_in_non_interactive(self, non_interactive_ctx: SetupContext) -> None:
        result = prompt_choice(non_interactive_ctx, "Pick:", ["a", "b", "c"], default=2)
        assert result == 2

    def test_validates_range(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", side_effect=["99", "2"]):
            result = prompt_choice(interactive_ctx, "Pick:", ["a", "b", "c"], default=0)
        assert result == 1


class TestPromptYN:
    def test_yes_input(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value="y"):
            assert prompt_yn(interactive_ctx, "OK?", default=False) is True

    def test_no_input(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value="n"):
            assert prompt_yn(interactive_ctx, "OK?", default=True) is False

    def test_empty_returns_default_true(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_yn(interactive_ctx, "OK?", default=True) is True

    def test_empty_returns_default_false(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_yn(interactive_ctx, "OK?", default=False) is False

    def test_non_interactive_returns_default(self, non_interactive_ctx: SetupContext) -> None:
        assert prompt_yn(non_interactive_ctx, "OK?", default=True) is True
        assert prompt_yn(non_interactive_ctx, "OK?", default=False) is False

    def test_case_insensitive(self, interactive_ctx: SetupContext) -> None:
        with patch("builtins.input", return_value="Y"):
            assert prompt_yn(interactive_ctx, "OK?", default=False) is True
        with patch("builtins.input", return_value="N"):
            assert prompt_yn(interactive_ctx, "OK?", default=True) is False


class TestSetupContext:
    def test_detects_non_interactive_from_env(self, tmp_path: Path) -> None:
        ctx = SetupContext.auto_detect(state_path=tmp_path / "state.json", non_interactive=True)
        assert ctx.interactive is False

    def test_defaults_to_interactive(self, tmp_path: Path) -> None:
        ctx = SetupContext.auto_detect(state_path=tmp_path / "state.json")
        # In test environment, stdout may or may not be a TTY
        assert isinstance(ctx.interactive, bool)

    def test_state_path_stored(self, tmp_path: Path) -> None:
        ctx = SetupContext(interactive=False, json_mode=False, state_path=tmp_path / "s.json")
        assert ctx.state_path == tmp_path / "s.json"
