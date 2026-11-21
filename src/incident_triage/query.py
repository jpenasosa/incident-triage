"""Turns free text into a safe SQLite FTS5 MATCH expression."""

import re

# Letters and digits in any script; everything else (FTS5 syntax included) is a separator.
_TERM = re.compile(r"[^\W_]+")


def build_match_query(q: str) -> str | None:
    """Return an FTS5 expression matching any term of `q`, or None if it has no terms.

    Each term is double-quoted, so FTS5 treats it as a literal string: operators
    (AND, OR, NOT, NEAR), column filters (`title:`), prefixes (`*`) and quotes in the
    input lose their meaning. BM25 ranks documents matching more terms higher.
    """
    terms = dict.fromkeys(t.lower() for t in _TERM.findall(q))
    return " OR ".join(f'"{t}"' for t in terms) or None
