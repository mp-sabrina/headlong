"""Mind log -> email, one follower thread per agent.

Follows one identity's root trajectory and sends message steps it
addressed to an email-* conversation as a reply, threaded with
In-Reply-To/References against the sender's original message. The
bridge's own inbound steps have an email-* `from` (not the identity), so
they never match — no echo loop. cli.py starts one of these per
configured agent, all sharing the one api.Client (and its two Resend
keys) but each reading only its own identity's trajectory and state.

The allowlist gates this direction too: an injected agent that emits a
step addressed to an unapproved (or unknown) conversation gets dropped
here, so it can't be used as a courier out to an arbitrary address — and
since sending only ever happens here, through api.Client, the identity
itself never needs a code path that can call Resend directly.
"""

from __future__ import annotations

import logging
import threading
import time

from . import emailfmt, mindlog, naming
from .allowlist import Allowlist
from .api import ApiError, Client
from .config import AgentConfig
from .threads import Threads

log = logging.getLogger(__name__)

DUPLICATE_WINDOW_SECONDS = 300


class RecentPosts:
    """Transport-level dedupe: agents occasionally send the same reply twice
    (e.g. an agentic run re-executing its chat command). Sending an
    identical reply to the same conversation twice within the window is
    never right.
    """

    def __init__(self, window: float = DUPLICATE_WINDOW_SECONDS):
        self._window = window
        self._last: dict[str, tuple[str, float]] = {}

    def is_duplicate(self, conversation: str, text: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        previous = self._last.get(conversation)
        if previous and previous[0] == text and now - previous[1] < self._window:
            return True
        self._last[conversation] = (text, now)
        return False


def run(
    agent: AgentConfig, api: Client, allowlist: Allowlist, threads: Threads, stop_event: threading.Event
) -> None:
    traj = mindlog.find_trajectory(agent.identity_dir)
    cursor = agent.state_dir / "cursor"
    recent = RecentPosts()
    log.info("[%s] following %s", agent.identity, traj)
    for step in mindlog.follow(traj, cursor, should_stop=stop_event.is_set):
        if step.get("type") != "message" or step.get("from") != agent.identity:
            continue
        if step.get("source") != "chat":
            # Only bin/chat speaks for the identity — it stamps source:"chat"
            # on every outgoing message. Thinkers sometimes append raw message
            # steps directly to the trajectory (thinking out loud, not a
            # reply); sending those would give the recipient a second,
            # unstamped voice. Bridges are the mouth; the trajectory is the
            # mind.
            log.warning(
                "[%s] dropping non-chat message step %s (source=%r)",
                agent.identity,
                step.get("step_id"),
                step.get("source"),
            )
            continue
        to = step.get("to")
        if not naming.is_email_name(to):
            continue
        entry = threads.get(to)
        if entry is None:
            log.warning(
                "[%s] dropping reply to unknown conversation %s (no thread record)", agent.identity, to
            )
            continue
        address = entry["address"]
        if not allowlist.is_approved(address):
            log.warning("[%s] dropping reply to unapproved address %s", agent.identity, address)
            continue
        text = emailfmt.strip_leaked_command(str(step.get("content") or "")).strip()
        if not text:
            continue
        if recent.is_duplicate(to, text):
            log.warning("[%s] skipping duplicate send to %s", agent.identity, to)
            continue
        references = list(entry.get("references") or [])
        last_message_id = entry.get("last_message_id")
        try:
            api.send(
                from_addr=agent.from_address,
                to=[address],
                subject=emailfmt.reply_subject(entry.get("subject", "")),
                text=text,
                in_reply_to=last_message_id,
                references=references,
            )
        except ApiError:
            log.exception("[%s] send failed for %s", agent.identity, to)


def start(
    agent: AgentConfig, api: Client, allowlist: Allowlist, threads: Threads, stop_event: threading.Event
) -> threading.Thread:
    thread = threading.Thread(
        target=run,
        args=(agent, api, allowlist, threads, stop_event),
        name=f"email-outbound-{agent.identity}",
        daemon=True,
    )
    thread.start()
    return thread
