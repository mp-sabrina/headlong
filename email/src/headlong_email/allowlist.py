"""The sender-address allowlist and its pending-approval queue.

Anyone on Earth can email an address they can guess, so the allowlist is
the bridge's real perimeter alongside the webhook signature: unknown
senders never reach the mind log, and outbound replies are only sent to
approved addresses. Managed entirely over email by the admin (/approve,
/deny, /revoke, /list — see inbound.py).

Persisted as one JSON file in the bridge state dir. In production that
dir should be owned by the bridge's own system user, not the agent's —
the same reasoning as telegram/src/headlong_telegram/allowlist.py: if the
agent's user could write it, an injected agent could approve an attacker
itself.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

# Notify the admin about a given unknown sender at most this often, so a
# stranger can't flood the admin's inbox with approval prompts.
PENDING_NOTIFY_INTERVAL = 24 * 3600


class Allowlist:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._approved: dict[str, str] = {}  # address -> display label
        self._denied: set[str] = set()
        self._pending: dict[str, dict] = {}  # address -> {label, last_notified}
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                self._approved = {str(k): str(v) for k, v in data.get("approved", {}).items()}
                self._denied = {str(a) for a in data.get("denied", [])}
                self._pending = {
                    str(k): {"label": str(v.get("label", "")), "last_notified": float(v.get("last_notified", 0))}
                    for k, v in data.get("pending", {}).items()
                }
            except (ValueError, TypeError, AttributeError):
                pass

    @staticmethod
    def _key(address: str) -> str:
        return address.strip().lower()

    def is_approved(self, address: str) -> bool:
        with self._lock:
            return self._key(address) in self._approved

    def approve(self, address: str, label: str = "") -> None:
        with self._lock:
            key = self._key(address)
            label = label or self._pending.get(key, {}).get("label", "") or key
            self._approved[key] = label
            self._pending.pop(key, None)
            self._denied.discard(key)
            self._save()

    def deny(self, address: str) -> None:
        """Silence a pending sender for good: drop with no further prompts."""
        with self._lock:
            key = self._key(address)
            self._denied.add(key)
            self._pending.pop(key, None)
            self._save()

    def revoke(self, address: str) -> bool:
        with self._lock:
            removed = self._approved.pop(self._key(address), None) is not None
            if removed:
                self._save()
            return removed

    def note_pending(self, address: str, label: str) -> bool:
        """Record an unknown sender; True if the admin should be notified now."""
        with self._lock:
            key = self._key(address)
            if key in self._approved or key in self._denied:
                return False
            now = time.time()
            entry = self._pending.get(key)
            if entry and now - entry["last_notified"] < PENDING_NOTIFY_INTERVAL:
                return False
            self._pending[key] = {"label": label, "last_notified": now}
            self._save()
            return True

    def summary(self) -> str:
        with self._lock:
            lines = [f"approved ({len(self._approved)}):"]
            lines += [f"  {addr}  {label}" for addr, label in sorted(self._approved.items())]
            if self._pending:
                lines.append(f"pending ({len(self._pending)}):")
                lines += [f"  {addr}  {v['label']}" for addr, v in sorted(self._pending.items())]
            if self._denied:
                lines.append(f"denied: {', '.join(sorted(self._denied))}")
            return "\n".join(lines)

    def _save(self) -> None:
        data = {
            "approved": self._approved,
            "denied": sorted(self._denied),
            "pending": self._pending,
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._path)
