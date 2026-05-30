#!/usr/bin/env python3
"""Generate 20 synthetic .ics files for the per-type fixture corpus.

ADR-028 measurement prereq. Hand-crafts the iCalendar text (RFC 5545) —
no extra dependency needed. Mix of one-off and recurring events.

The ``weekly-standup-our-team.ics`` event series carries three weekly
occurrences with different attendees — the canary asserts "when does
the weekly standup move next" surfaces both the recurring rule AND the
exception entries.

Run from repo root:
    python3 scripts/reflib/generate_calendar_fixtures.py

Output: reference-library/per-type-fixtures/calendar/*.ics (20 files)
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent))

from scripts.reflib._fixture_vocab import (  # noqa: E402 — sys.path tweak above
    AGENTS,
    PROJECTS,
    QUARTERS,
    TOPICS,
)

SEED = 28028
N_FIXTURES = 20


def _ics_event(
    *,
    uid: str,
    summary: str,
    description: str,
    start: str,
    end: str,
    attendees: list[str],
    rrule: str | None = None,
) -> str:
    """Compose a single VCALENDAR / VEVENT block as a string."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//kairix//per-type-fixtures//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}@example.invalid",
        f"DTSTAMP:{start}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
    ]
    for attendee in attendees:
        lines.append(f"ATTENDEE;CN={attendee}:mailto:{attendee}@example.invalid")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _build_canary_recurring_series(output_dir: Path) -> int:
    """The weekly-standup canary series — 3 events on different dates.

    Canary query: "when does the weekly standup move next?"
    Correct answer requires retrieving the event-2 record (the moved one)
    AND understanding it differs from event-1's recurrence.
    """
    written = 0
    output_dir.joinpath("weekly-standup-our-team-week-01.ics").write_text(
        _ics_event(
            uid="weekly-standup-week-01",
            summary="Weekly standup — our-team",
            description="Recurring weekly standup. Project: project-falcon. "
            "Default time: Monday 09:00. Attendees: full team.",
            start="20260504T090000Z",
            end="20260504T093000Z",
            attendees=["agent-alpha", "agent-beta", "agent-gamma"],
            rrule="FREQ=WEEKLY;BYDAY=MO",
        ),
        encoding="utf-8",
    )
    written += 1
    output_dir.joinpath("weekly-standup-our-team-week-02.ics").write_text(
        _ics_event(
            uid="weekly-standup-week-02",
            summary="Weekly standup — our-team (MOVED)",
            description="Standup moved to Tuesday this week. "
            "Original Monday slot conflicts with project-falcon review. "
            "Next move: back to Monday from week 3.",
            start="20260512T090000Z",
            end="20260512T093000Z",
            attendees=["agent-alpha", "agent-beta", "agent-gamma"],
        ),
        encoding="utf-8",
    )
    written += 1
    output_dir.joinpath("weekly-standup-our-team-week-03.ics").write_text(
        _ics_event(
            uid="weekly-standup-week-03",
            summary="Weekly standup — our-team",
            description="Back to the Monday default slot. Attendees: full team.",
            start="20260518T090000Z",
            end="20260518T093000Z",
            attendees=["agent-alpha", "agent-beta", "agent-gamma"],
        ),
        encoding="utf-8",
    )
    written += 1
    return written


def _build_generic_event(output: Path, rng: random.Random, idx: int) -> None:
    project, _focus = rng.choice(PROJECTS)
    topic = rng.choice(TOPICS)
    quarter = rng.choice(QUARTERS)
    summary = f"{project} — {topic} ({quarter})"
    description = f"Working session on {topic} for {project} in {quarter}. Driver: {rng.choice(AGENTS)}."
    # Stagger times across May 2026
    day = (idx % 28) + 1
    start = f"202605{day:02d}T140000Z"
    end = f"202605{day:02d}T150000Z"
    attendees = rng.sample(AGENTS, k=rng.randint(2, 4))
    is_recurring = idx % 4 == 0
    rrule = "FREQ=WEEKLY;COUNT=4" if is_recurring else None
    output.write_text(
        _ics_event(
            uid=f"generic-event-{idx:03d}",
            summary=summary,
            description=description,
            start=start,
            end=end,
            attendees=list(attendees),
            rrule=rrule,
        ),
        encoding="utf-8",
    )


def generate(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    written = 0
    written += _build_canary_recurring_series(output_dir)
    remaining = N_FIXTURES - written
    for i in range(remaining):
        _build_generic_event(output_dir / f"generic-event-{i + 1:03d}.ics", rng, i)
        written += 1
    return written


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = repo_root / "reference-library" / "per-type-fixtures" / "calendar"
    n = generate(output_dir)
    print(f"calendar fixtures: wrote {n} files to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
