"""Shared parser for the LLM's `SUBJECT: ... / BODY: ...` output format.

Used by compose, follow-up scheduling, and the generator's reserialization step
so the parsing rules can't drift between call sites.

Tolerant of the decoration small local models add around the markers:
`**SUBJECT:** hi`, `# SUBJECT: hi`, `> BODY:`, `Subject - hi`, etc.
"""

import re

# A marker line: optional markdown noise, the keyword, then ':' or '-'.
#   group(1) = keyword, group(2) = rest of the line after the delimiter
_MARKER_RE = re.compile(
    r"^[\s>#*_\-`]*(SUBJECT|BODY)\b[\s*_`]*[:\-–][\s*_`]*(.*?)[\s*_`]*$",
    re.IGNORECASE,
)


def parse_subject_body(text: str, fallback_subject: str = "") -> tuple[str, str]:
    """Extract (subject, body) from an LLM email response.

    Recognises `SUBJECT:` / `BODY:` markers (case-insensitive, tolerating
    markdown bold/heading/quote decoration around them). If no markers are
    found, the whole text is treated as the body and `fallback_subject` is used.
    """
    subject, body = fallback_subject, (text or "").strip()
    lines = body.splitlines()
    subject_line = -1

    for i, line in enumerate(lines):
        m = _MARKER_RE.match(line)
        if not m:
            continue
        keyword = m.group(1).upper()
        rest = m.group(2).strip()
        if keyword == "SUBJECT":
            subject = rest or fallback_subject
            subject_line = i
        else:  # BODY — inline content (rare) plus everything below
            tail = "\n".join(lines[i + 1:]).strip()
            body = f"{rest}\n{tail}".strip() if rest else tail
            return subject, body

    # SUBJECT found but no BODY marker: body = everything after the subject line.
    if subject_line >= 0:
        body = "\n".join(lines[subject_line + 1:]).strip()

    return subject, body
