"""Step definitions for chunk_date_fallback.feature."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from pytest_bdd import given, parsers, then, when

from kairix.core.embed.date_extract import extract_chunk_date

# Module-level state (simple, test-scoped)
_state: dict = {}


@given(parsers.parse('a document with frontmatter date "{fm_date}"'))
def given_doc_with_frontmatter(fm_date):
    _state["doc"] = f"---\ndate: {fm_date}\n---\nSome document content."
    _state["path"] = "notes/plain.md"
    _state["document_root"] = None
    _state["tmp_dir"] = None


@given(parsers.parse('a document with no frontmatter date and file mtime "{mtime_date}"'))
def given_doc_no_frontmatter_with_mtime(mtime_date):
    _state["doc"] = "No frontmatter here. Just plain text."
    _state["path"] = "plain.md"

    # Create a real temp file so mtime fallback works
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, "plain.md")
    with open(filepath, "w") as f:
        f.write(_state["doc"])

    # Set file mtime to the desired date
    dt = datetime.strptime(mtime_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    os.utime(filepath, (ts, ts))

    _state["document_root"] = tmp_dir
    _state["tmp_dir"] = tmp_dir


@given(parsers.re(r'a document at path "(?P<path>[^"]*)" with file mtime "(?P<mtime_date>[^"]*)"'))
def given_doc_at_path_with_mtime(path, mtime_date):
    _state["doc"] = "No frontmatter here. Just plain text."
    _state["path"] = path

    # Create a real temp file at the given subpath
    tmp_dir = tempfile.mkdtemp()
    full_path = os.path.join(tmp_dir, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(_state["doc"])

    dt = datetime.strptime(mtime_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = dt.timestamp()
    os.utime(full_path, (ts, ts))

    _state["document_root"] = tmp_dir
    _state["tmp_dir"] = tmp_dir


@when("chunk date is extracted")
def extract_date():
    _state["result"] = extract_chunk_date(
        doc=_state["doc"],
        path=_state["path"],
    )


@when("chunk date is extracted with document root")
def extract_date_with_root():
    _state["result"] = extract_chunk_date(
        doc=_state["doc"],
        path=_state["path"],
        document_root=_state["document_root"],
    )


@then(parsers.parse('the chunk date is "{expected}"'))
def chunk_date_equals(expected):
    assert _state["result"] == expected, f"Expected {expected!r}, got {_state['result']!r}"
