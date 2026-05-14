"""Render postmortems into a 'Known pitfalls' section with a hard char cap.

Phase 2A — read path only. The renderer takes ``sqlite3.Row`` rows from
``query_active_postmortems`` (or any caller that supplies the same column
shape) and emits a Markdown bullet list ready to append to a system prompt.

Truncation contract:
    * Caller is expected to pre-sort rows by *priority* —
      ``query_active_postmortems`` orders by ``confidence DESC, created_at DESC``.
      The renderer iterates that order and routes any bullet that would
      push past ``max_chars`` into ``dropped`` instead of ``bullets``.
    * Result: lowest-priority rows are dropped first when ``rows`` come
      from the canonical query.
    * Empty ``rows`` returns ``("", [])`` — caller decides not to append
      an empty header.

Budget rationale (Appendix E.8): 8,000 chars ≈ 2,000 tokens at the typical
4 chars/token ratio. That keeps pitfalls comfortably inside the cacheable
static block of the system prompt without crowding out agent-specific
instructions.
"""

from __future__ import annotations

import sqlite3
from typing import Sequence

_HEADER = "## Known pitfalls\n"
MAX_PITFALL_CHARS = 8000


def render_pitfalls_section(
    rows: Sequence[sqlite3.Row],
    *,
    max_chars: int = MAX_PITFALL_CHARS,
) -> tuple[str, list[int]]:
    """Render a Markdown 'Known pitfalls' section.

    Args:
        rows: Postmortem rows. Must support keyed access (``row['id']``,
            ``row['stack_type']``, ``row['agent']``, ``row['confidence']``,
            ``row['failure_signature']``, ``row['context_excerpt']``).
            ``conn.row_factory`` must be ``sqlite3.Row`` upstream.
        max_chars: Hard cap on the rendered section length, in characters.
            Default ``MAX_PITFALL_CHARS`` (8,000 ≈ 2,000 tokens).

    Returns:
        A pair ``(section, dropped_ids)``:
            * ``section`` — the rendered Markdown, or ``""`` when ``rows``
              is empty.
            * ``dropped_ids`` — IDs of rows that did not fit under
              ``max_chars``, in input order. Caller can publish a
              ``PromptBudgetExceeded`` event with this list.
    """
    if not rows:
        return "", []

    bullets: list[str] = []
    dropped: list[int] = []
    running = len(_HEADER) + 1  # leading newline after header before bullets

    for row in rows:
        excerpt = (row["context_excerpt"] or "")[:200]
        bullet = (
            f"- **[postmortem:{row['id']} stack:{row['stack_type']} "
            f"agent:{row['agent']} conf:{row['confidence']}]** "
            f"{row['failure_signature']}\n"
            f"  {excerpt}\n"
        )
        if running + len(bullet) > max_chars:
            dropped.append(row["id"])
            continue
        bullets.append(bullet)
        running += len(bullet)

    section = _HEADER + "\n" + "".join(bullets)
    return section, dropped
