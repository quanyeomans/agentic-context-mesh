"""Security helpers for the eval module — path confinement and prompt-injection
sanitisers.

Phase 0b of #143. Two threats this module addresses:

1. **Path traversal (S2083).** CLI flags and suite-YAML fields can carry
   ``../../etc/passwd`` or other escapes. ``confine_to(root, candidate)``
   resolves ``candidate`` against ``root`` and verifies the result stays
   inside ``root``. Raises ``PathTraversalError`` (a ``ValueError`` subclass)
   on escape.

2. **Prompt injection.** Document content sent to the LLM judge / query
   generator may carry adversarial role-marker tokens (``<|im_start|>``,
   ``<<SYS>>``, ``[INST]`` etc.) that some models honour as control tokens.
   ``sanitise_document_content(text, *, cap)`` strips/escapes those tokens,
   removes newlines, and truncates to ``cap`` characters. Use it at every
   site where untrusted vault content is interpolated into an LLM prompt.

Both helpers are kept tiny and pure so they are trivially auditable. The
documented threat model: vault content is **trusted-but-adversarial** — the
operator controls what's in the corpus, but cannot guarantee no document was
edited by a hostile party. The eval module must defend itself even when the
content is local.
"""

from __future__ import annotations

import logging
import re

# Path confinement converged onto the canonical home in ``kairix.paths`` so the
# eval module and every CLI share ONE auditable allow-list sanitiser. Re-exported
# here for backward compatibility — existing importers of
# ``kairix.quality.eval.security.confine_to`` keep working unchanged.
from kairix.paths import PathTraversalError, confine_to

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PROMPT_SNIPPET_CAP",
    "PathTraversalError",
    "confine_to",
    "sanitise_document_content",
]

# Default cap for any single document snippet interpolated into a prompt.
# 1000 chars is long enough to capture relevance signal for the judge,
# short enough to stop unbounded escalation if an adversary stuffs a
# document with role-marker payload.
DEFAULT_PROMPT_SNIPPET_CAP: int = 1000

# Role-marker tokens that some LLMs honour as control sequences. Stripping
# them out prevents an adversarial document from breaking out of the
# delimited ``<document>...</document>`` envelope. Keep this list narrow and
# explicit — broad regex sweeps risk mangling legitimate corpus content.
_ROLE_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|[^|>]{0,40}\|>"),  # ChatML / Llama: <|im_start|>, <|im_end|>, <|system|>
    re.compile(r"<<SYS>>|<</SYS>>"),  # Llama-2 system markers
    re.compile(r"\[INST\]|\[/INST\]"),  # Llama-2 instruction markers
    re.compile(r"<\|endoftext\|>"),  # OpenAI legacy EOT
)


def sanitise_document_content(text: str, *, cap: int = DEFAULT_PROMPT_SNIPPET_CAP) -> str:
    """Sanitise untrusted document content before interpolation into an LLM prompt.

    Three defences, in order:

    1. Strip role-marker tokens (``<|im_start|>``, ``<<SYS>>``, ``[INST]``,
       etc.) that some models honour as control sequences.
    2. Replace literal newlines / carriage returns with spaces so adversarial
       content cannot break out of a one-line tag envelope.
    3. Truncate to ``cap`` characters to bound the attack surface.

    The output is safe to interpolate inside ``<document>...</document>``
    tags as long as the surrounding system prompt instructs the model to
    treat content inside the tags as data, never instructions (see
    :func:`kairix.quality.eval.judge.LLMJudge._build_prompt`).

    Args:
        text: Raw document snippet from the vault / corpus.
        cap:  Maximum output length in characters. Defaults to
              :data:`DEFAULT_PROMPT_SNIPPET_CAP`.

    Returns:
        Sanitised single-line string of at most ``cap`` characters.
    """
    if not text:
        return ""
    cleaned = text
    for pattern in _ROLE_MARKER_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    return cleaned[:cap]
