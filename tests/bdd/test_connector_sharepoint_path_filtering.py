"""pytest-bdd test module for connector_sharepoint_path_filtering.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_sharepoint_path_filtering.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "A single include path scopes the drive to one folder")
def test_single_include_path_scopes_drive() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Multiple include paths combine as a union")
def test_multiple_includes_combine() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Empty include_paths preserves the current whole-drive behaviour")
def test_empty_preserves_behaviour() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Exclude path overrides an overlapping include path")
def test_exclude_overrides_include() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Exclude path with no include path still filters")
def test_standalone_exclude_filters() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An include path that doesn't exist in the drive warns at startup and skips at runtime")
def test_missing_include_warns() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An include path matches the folder envelope itself plus descendants")
def test_include_matches_folder_envelope() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Prefix matching respects path-segment boundaries")
def test_segment_boundary_match() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Filter active on an empty drive emits zero events without error")
def test_empty_drive_no_error() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Cursor format is unaffected by include_paths")
def test_cursor_unaffected() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An item that moved out of an included path between sync passes drops at the filter")
def test_move_out_drops() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An item that moved into an included path emits as a created event")
def test_move_in_emits_created() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A rename within an included path emits modified as usual")
def test_rename_emits_modified() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An LLM-driven setup agent can read the include_paths schema via the MCP config-validate tool")
def test_agent_reads_include_paths_schema() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An LLM-driven status query surfaces the active path filters")
def test_agent_status_surfaces_filters() -> None:
    """Body populated by @scenario from the .feature file."""
