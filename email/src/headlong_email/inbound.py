"""Resend webhook events -> mind log.

Every request reaching this module already passed signature verification
(server.py) and JSON parsing — that's what makes it safe to trust the
sender/recipient addresses it reports. From there: figure out which
configured agent the mail was addressed to, then the same gate as the
Telegram bridge applies within that agent's own trust circle — admin
commands, then its allowlist, then delivery.

The webhook itself only carries event metadata (from, subject, an
email_id); the body has to be fetched with a follow-up API call before
there's any text to gate or deliver. That follow-up call, and every
outbound send, goes through api.Client — the only place the Resend keys
are ever used. Nothing here, and nothing in the agent's own reply path
(it just writes "chat reply <conv_id> ..." like any other bridge), ever
sees a Resend key or talks to Resend directly.
"""

from __future__ import annotations

import logging
import re
import time
from email.utils import parseaddr
from typing import Any

import httpx

from . import emailfmt, naming
from .allowlist import Allowlist
from .api import ApiError, Client
from .config import AgentConfig, SharedConfig
from .runtime import AgentRuntime
from .threads import Threads

log = logging.getLogger(__name__)

DELIVERY_ATTEMPTS = 3
DELIVERY_ERROR_TEXT = (
    "(bridge error: I couldn't reach my mind just now — please try again in a bit)"
)

ADMIN_HELP = (
    "headlong email bridge admin commands (reply with one per message):\n"
    "/approve <address> — let this sender talk to the identity\n"
    "/deny <address> — silence a pending request for good\n"
    "/revoke <address> — remove an approved sender\n"
    "/list — show approved + pending senders\n"
    "Anything else you send goes to the identity as a normal message."
)

# Dedup window: Resend retries a webhook delivery that didn't 2xx, and could
# in principle send the same event twice for other reasons too.
_DEDUP_WINDOW_SECONDS = 3600

# Matches the first email-looking substring in an admin command's argument,
# so "/approve addr@x.com<mailto:addr@x.com>" (a mail client's plain-text
# rendering of an auto-linked address) resolves to "addr@x.com", not the
# whole garbled token.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class Inbound:
    def __init__(self, shared: SharedConfig, api: Client, agents: dict[str, AgentRuntime]):
        self.shared = shared
        self.api = api
        self.agents = agents  # lowercased from_address -> AgentRuntime
        self._seen_email_ids: dict[str, float] = {}

    # -- entry point, called from the webhook route --------------------------

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "email.received":
            return
        data = event.get("data") or {}
        email_id = data.get("email_id") or data.get("id")
        if not email_id:
            log.warning("email.received event with no email_id: %r", event)
            return
        if self._is_duplicate(email_id):
            log.info("dropping duplicate email_id %s", email_id)
            return
        try:
            full = self.api.get_received_email(email_id)
        except ApiError:
            log.exception("failed fetching received email %s", email_id)
            return
        self._handle(full)

    def _is_duplicate(self, email_id: str) -> bool:
        now = time.monotonic()
        if email_id in self._seen_email_ids:
            return True
        self._seen_email_ids[email_id] = now
        cutoff = now - _DEDUP_WINDOW_SECONDS
        self._seen_email_ids = {k: v for k, v in self._seen_email_ids.items() if v > cutoff}
        return False

    # -- which agent this mail belongs to ---------------------------------

    def _resolve_agent(self, full: dict[str, Any]) -> AgentRuntime | None:
        """Match the email's recipient(s) against configured agent addresses.

        An identity must only ever see mail sent to its own address — this
        is that boundary. `to` is the primary field; `received_for` covers
        a forwarded copy. Anything addressed to an unconfigured address on
        the domain (a typo, an old identity that was retired) is dropped.
        """
        candidates: list[str] = []
        to = full.get("to")
        candidates += to if isinstance(to, list) else [to] if isinstance(to, str) else []
        received_for = full.get("received_for")
        candidates += (
            received_for
            if isinstance(received_for, list)
            else [received_for]
            if isinstance(received_for, str)
            else []
        )
        for raw in candidates:
            _, address = parseaddr(str(raw))
            runtime = self.agents.get(address.strip().lower())
            if runtime:
                return runtime
        return None

    # -- the gate --------------------------------------------------------------

    def _handle(self, full: dict[str, Any]) -> None:
        runtime = self._resolve_agent(full)
        if runtime is None:
            log.warning(
                "email.received for an address with no configured agent (to=%r, received_for=%r)",
                full.get("to"),
                full.get("received_for"),
            )
            return
        agent = runtime.agent

        display_name, address = parseaddr(str(full.get("from") or ""))
        address = address.strip().lower()
        if not address:
            return
        subject = str(full.get("subject") or "")
        message_id = full.get("message_id")
        text = full.get("text") or ""
        if not text:
            log.warning("dropping html-only or empty email from %s", address)
            return
        content = emailfmt.clean_inbound(text)
        if not content:
            return

        if address == agent.admin_address and self._admin_command(agent, runtime.allowlist, content):
            return
        if not runtime.allowlist.is_approved(address):
            self._pending(agent, runtime.allowlist, address, display_name)
            return
        self._deliver(agent, runtime.threads, address, display_name, subject, message_id, content)

    # -- admin commands (intercepted; never reach the mind log) ----------------

    def _admin_command(self, agent: AgentConfig, allowlist: Allowlist, content: str) -> bool:
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        parts = first_line.split()
        command = parts[0].lower() if parts else ""
        if command not in ("/approve", "/deny", "/revoke", "/list", "/help"):
            return False
        reply = ADMIN_HELP
        if command in ("/approve", "/deny", "/revoke"):
            # Extract a clean address rather than trusting the argument
            # verbatim: a mail client that auto-linkifies a typed address
            # can hand us "addr@x.com<mailto:addr@x.com>" as the plain-text
            # line, and a naive whitespace split would silently approve
            # that whole garbled string instead of the real address.
            match = _EMAIL_RE.search(" ".join(parts[1:]))
            if not match:
                reply = f"usage: {command} <address>"
            else:
                target = match.group(0).lower()
                if command == "/approve":
                    allowlist.approve(target)
                    reply = f"approved {target}"
                elif command == "/deny":
                    allowlist.deny(target)
                    reply = f"denied {target} (silenced for good)"
                else:
                    reply = f"revoked {target}" if allowlist.revoke(target) else f"{target} wasn't approved"
        elif command == "/list":
            reply = allowlist.summary()
        self._send_bridge_mail(agent, agent.admin_address, "Re: headlong email bridge", reply)
        return True

    # -- unknown senders ---------------------------------------------------------

    def _pending(self, agent: AgentConfig, allowlist: Allowlist, address: str, display_name: str) -> None:
        # Silent to the sender: replying would confirm a live bot to whoever
        # is probing it. The admin gets one prompt per sender per day.
        label = display_name or address
        if allowlist.note_pending(address, label):
            self._send_bridge_mail(
                agent,
                agent.admin_address,
                f"headlong email bridge ({agent.identity}): new sender",
                f"{label} <{address}> wants to talk — "
                f"reply /approve {address} or /deny {address}",
            )

    # -- delivery ------------------------------------------------------------

    def _deliver(
        self,
        agent: AgentConfig,
        threads: Threads,
        address: str,
        display_name: str,
        subject: str,
        message_id: str | None,
        content: str,
    ) -> None:
        conv_id = naming.encode(address)
        threads.remember_inbound(conv_id, address, display_name, subject, message_id)
        label = display_name or address
        header = (
            f'(Email: {label} <{address}>, subject "{subject}"'
            f" — reply with: chat reply {conv_id})"
        )
        body = {"content": f"{header}\n{content}", "from_name": conv_id}
        chat_url = f"{self.shared.web_url}/api/identities/{agent.identity_api_id}/chat"
        for attempt in range(1, DELIVERY_ATTEMPTS + 1):
            try:
                response = httpx.post(chat_url, json=body, timeout=30)
                response.raise_for_status()
                return
            except httpx.HTTPError:
                log.warning(
                    "chat POST failed (attempt %d/%d)",
                    attempt,
                    DELIVERY_ATTEMPTS,
                    exc_info=True,
                )
                if attempt < DELIVERY_ATTEMPTS:
                    time.sleep(2 * attempt)
        self._send_bridge_mail(
            agent, address, emailfmt.reply_subject(subject), DELIVERY_ERROR_TEXT, in_reply_to=message_id
        )

    # -- bridge-generated mail (admin notices, error replies) ------------------

    def _send_bridge_mail(
        self, agent: AgentConfig, to: str, subject: str, text: str, in_reply_to: str | None = None
    ) -> None:
        try:
            self.api.send(
                from_addr=agent.from_address,
                to=[to],
                subject=subject,
                text=text,
                in_reply_to=in_reply_to,
            )
        except ApiError:
            log.exception("failed sending bridge mail to %s", to)
