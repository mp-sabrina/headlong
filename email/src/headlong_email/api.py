"""Minimal Resend REST client — just what the bridge needs.

Takes a single API key, used for both sending and reading received mail.
Reading a received email's body appears to require a `full_access` key
(a `sending_access` key can be scoped to send-only + one domain, but has
no read access), which makes splitting into two keys pointless — the one
key this Client holds already has to be the most-privileged kind either
way. See email/README.md ("Keeping the Resend key away from the agent")
for what that does and doesn't protect against, and only this module and
its two callers (inbound.py, outbound.py) ever use it.

https://resend.com/docs/api-reference/emails/retrieve-received-email
https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

import httpx

BASE_URL = "https://api.resend.com"


class ApiError(Exception):
    pass


class Client:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout

    def get_received_email(self, email_id: str) -> dict:
        """The webhook only carries metadata; this fetches subject/text/etc."""
        resp = httpx.get(
            f"{BASE_URL}/emails/receiving/{email_id}",
            headers=self._headers,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise ApiError(f"GET emails/receiving/{email_id}: {resp.status_code} {resp.text}")
        return resp.json()

    def send(
        self,
        *,
        from_addr: str,
        to: list[str],
        subject: str,
        text: str,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> dict:
        payload: dict = {"from": from_addr, "to": to, "subject": subject, "text": text}
        headers = {}
        if in_reply_to:
            headers["In-Reply-To"] = in_reply_to
        if references:
            headers["References"] = " ".join(references)
        if headers:
            payload["headers"] = headers
        resp = httpx.post(
            f"{BASE_URL}/emails",
            headers=self._headers,
            json=payload,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise ApiError(f"POST /emails: {resp.status_code} {resp.text}")
        return resp.json()
