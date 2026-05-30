#!/usr/bin/env python3
"""Generate 30 synthetic .eml files for the per-type fixture corpus.

ADR-028 measurement prereq. Uses email.message.EmailMessage from the
stdlib — no extra dependency. Mix of subject-heavy, body-heavy, and
threaded messages.

Three messages in a single thread (subject "Re: project-falcon
go/no-go") form the boundary-spanning canary partners — the canary
asserts the consensus across all three messages surfaces.

Run from repo root:
    python3 scripts/reflib/generate_email_fixtures.py

Output: reference-library/per-type-fixtures/email/*.eml (30 files)
"""

from __future__ import annotations

import random
import sys
from email.message import EmailMessage
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
N_FIXTURES = 30


def _write_message(
    output: Path,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    message_id: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = f"{sender}@example.invalid"
    msg["To"] = f"{to}@example.invalid"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 04 May 2026 09:00:00 +0000"
    if message_id:
        msg["Message-ID"] = f"<{message_id}@example.invalid>"
    if in_reply_to:
        msg["In-Reply-To"] = f"<{in_reply_to}@example.invalid>"
        msg["References"] = f"<{in_reply_to}@example.invalid>"
    msg.set_content(body)
    output.write_bytes(bytes(msg))


def _build_canary_thread(output_dir: Path) -> int:
    """Three-message thread — the consensus appears across all three.

    Canary query: "what did the team agree about project-falcon go/no-go?"
    Correct answer requires retrieving M1 + M2 + M3 — M1 raises the
    question, M2 proposes a position, M3 confirms the team agreement.
    """
    subject = "Re: project-falcon go/no-go"
    _write_message(
        output_dir / "canary-thread-msg-01.eml",
        sender="agent-alpha",
        to="agent-beta",
        subject=subject,
        body=(
            "Team — circling back on the project-falcon go/no-go decision.\n"
            "What does everyone think about pushing to next cycle?\n"
            "We have the architecture spike landing this week.\n"
        ),
        message_id="canary-thread-msg-01",
    )
    _write_message(
        output_dir / "canary-thread-msg-02.eml",
        sender="agent-beta",
        to="agent-alpha",
        subject=subject,
        body=(
            "I think we should proceed with project-falcon as planned.\n"
            "The spike is on track and the cutover risk is bounded.\n"
            "Proposing: green-light now, soak for 24h, promote after the gate.\n"
        ),
        in_reply_to="canary-thread-msg-01",
        message_id="canary-thread-msg-02",
    )
    _write_message(
        output_dir / "canary-thread-msg-03.eml",
        sender="agent-gamma",
        to="agent-alpha",
        subject=subject,
        body=(
            "Agreed. We're a go on project-falcon.\n"
            "Owner: agent-alpha. Soak window: 24h. Promotion: after gate.\n"
            "Team agreement recorded — closing this thread.\n"
        ),
        in_reply_to="canary-thread-msg-02",
        message_id="canary-thread-msg-03",
    )
    return 3


def _build_generic_email(output: Path, rng: random.Random, idx: int) -> None:
    sender = rng.choice(AGENTS)
    to = rng.choice([a for a in AGENTS if a != sender])
    project, _focus = rng.choice(PROJECTS)
    topic = rng.choice(TOPICS)
    quarter = rng.choice(QUARTERS)
    shape = rng.choice(["subject_heavy", "body_heavy", "balanced"])
    if shape == "subject_heavy":
        subject = f"[{project}/{quarter}] {topic} — review request for the {topic} action"
        body = "See subject.\n"
    elif shape == "body_heavy":
        subject = f"{project} update"
        body = "\n".join(
            f"Para {p}: covering {rng.choice(TOPICS)} for {project} in {quarter}. "
            f"Owner is {rng.choice(AGENTS)}; action: schedule the {rng.choice(TOPICS)} review."
            for p in range(rng.randint(4, 8))
        )
    else:
        subject = f"{project} — {topic} ({quarter})"
        body = f"Quick note on {topic} for {project} in {quarter}. Owner: {rng.choice(AGENTS)}.\n"
    _write_message(output, sender=sender, to=to, subject=subject, body=body, message_id=f"generic-{idx:03d}")


def generate(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    written = 0
    # Canary thread (3 messages)
    written += _build_canary_thread(output_dir)
    # Remaining generic emails
    remaining = N_FIXTURES - written
    for i in range(remaining):
        _build_generic_email(output_dir / f"generic-email-{i + 1:03d}.eml", rng, i)
        written += 1
    return written


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = repo_root / "reference-library" / "per-type-fixtures" / "email"
    n = generate(output_dir)
    print(f"email fixtures: wrote {n} files to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
