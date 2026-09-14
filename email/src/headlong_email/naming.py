"""Encode an email conversation into a chat `from` name.

The web API's CHAT_FROM_RE (^[A-Za-z0-9][A-Za-z0-9._-]*$) can't hold a raw
address (the `@` is illegal), so the name is a deterministic, sanitized
slug of the sender's local-part plus a hash suffix — the suffix keeps two
addresses that sanitize to the same slug (e.g. "a.b@x.com" and "a-b@y.com")
from colliding, and makes the name reproducible for a given address without
needing a lookup to produce it.

Unlike the Telegram bridge's naming.decode(), this can't be turned back
into an address by itself — the hash isn't reversible. Reply delivery
looks the address, subject, and threading headers up in `threads.py` by
this same id.
"""

from __future__ import annotations

import hashlib
import re

PREFIX = "email"
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def encode(address: str) -> str:
    address = address.strip().lower()
    local = address.split("@", 1)[0]
    slug = _SLUG_RE.sub("", local)[:24] or "sender"
    digest = hashlib.sha256(address.encode()).hexdigest()[:10]
    return f"{PREFIX}-{slug}-{digest}"


def is_email_name(name: object) -> bool:
    return isinstance(name, str) and name.startswith(f"{PREFIX}-") and len(name) > len(PREFIX) + 1
