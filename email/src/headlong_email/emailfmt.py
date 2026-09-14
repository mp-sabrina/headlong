"""Text handling for the email bridge.

Deliberately minimal, plain-text only for v1: no markdown/HTML conversion.
"""

from __future__ import annotations

import re

_QUOTE_HEADER_RE = re.compile(r"^\s*On .{0,160} wrote:\s*$", re.MULTILINE)
_ORIGINAL_MSG_RE = re.compile(r"^[-_]{2,}\s*Original Message\s*[-_]{2,}", re.MULTILINE | re.IGNORECASE)
_LEAKED_COMMAND_RE = re.compile(r"^\s*chat reply [A-Za-z0-9._-]+\s*", re.IGNORECASE)


def clean_inbound(text: str) -> str:
    """Strip quoted reply history so the mind log only sees the new text.

    Cuts at the first "On ... wrote:" or "----- Original Message -----"
    marker most mail clients insert above quoted history, then drops any
    trailing lines that are themselves `>`-quoted (a client that didn't use
    either marker but did prefix every quoted line).
    """
    text = text.replace("\r\n", "\n")
    for pattern in (_QUOTE_HEADER_RE, _ORIGINAL_MSG_RE):
        match = pattern.search(text)
        if match:
            text = text[: match.start()]
    lines = text.split("\n")
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    return "\n".join(lines).strip()


def strip_leaked_command(text: str) -> str:
    """Drop a leading 'chat reply <name>' the model echoed into its reply."""
    return _LEAKED_COMMAND_RE.sub("", text, count=1)


def reply_subject(subject: str) -> str:
    subject = (subject or "").strip()
    if not subject:
        return "Re: (no subject)"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
