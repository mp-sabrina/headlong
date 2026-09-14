"""Persisted address book: conversation id -> reply routing info.

naming.encode() is one-way (the id can't be turned back into an address),
so outbound delivery looks the sender's address and current threading
headers up here by id. Updated on every inbound message; read on every
outbound reply. Persisted as one JSON file in the bridge state dir, same
shape as the Telegram bridge's allowlist.json.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

# Keep the References header from growing without bound on a long thread.
MAX_REFERENCES = 20


class Threads:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        if path.is_file():
            try:
                self._data = json.loads(path.read_text())
            except (ValueError, TypeError):
                pass

    def get(self, conv_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._data.get(conv_id)
            return dict(entry) if entry else None

    def remember_inbound(
        self,
        conv_id: str,
        address: str,
        display_name: str,
        subject: str,
        message_id: str | None,
    ) -> None:
        with self._lock:
            entry = self._data.setdefault(
                conv_id,
                {"address": address, "name": "", "subject": "", "references": []},
            )
            entry["address"] = address
            if display_name:
                entry["name"] = display_name
            if subject:
                entry["subject"] = subject
            if message_id:
                refs = entry.setdefault("references", [])
                if message_id not in refs:
                    refs.append(message_id)
                entry["references"] = refs[-MAX_REFERENCES:]
                entry["last_message_id"] = message_id
            self._save()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self._path)
