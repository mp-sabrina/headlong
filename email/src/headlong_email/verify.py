"""Verify Resend webhook signatures.

Resend signs webhooks the Svix way: three headers carry the proof
(svix-id, svix-timestamp, svix-signature). The signed content is
"<id>.<timestamp>.<raw body>", HMAC-SHA256'd with the base64 portion of
the whsec_... secret, then base64-encoded again. svix-signature can carry
several space-separated "v1,<sig>" tokens (secret rotation sends more than
one); any match is accepted. The timestamp is bound to a tolerance window
so a captured, still-technically-valid signature can't be replayed much
later.

https://resend.com/docs/dashboard/webhooks/verify-webhooks-requests

This is the bridge's whole perimeter against forged inbound mail: nothing
downstream re-checks who is allowed to make an HTTP POST to the webhook
route, so a signature bypass here would let anyone inject arbitrary
"emails" into the mind log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 5 * 60


class VerificationError(Exception):
    pass


def verify(
    body: bytes,
    headers: dict[str, str],
    secret: str,
    now: float | None = None,
) -> None:
    """Raise VerificationError unless body+headers were signed with secret.

    `headers` is looked up case-insensitively assuming lowercase keys
    (Starlette/FastAPI's Headers already normalize this way).
    """
    lower = {k.lower(): v for k, v in headers.items()}
    svix_id = lower.get("svix-id")
    svix_timestamp = lower.get("svix-timestamp")
    svix_signature = lower.get("svix-signature")
    if not svix_id or not svix_timestamp or not svix_signature:
        raise VerificationError("missing svix-id/svix-timestamp/svix-signature header")

    try:
        ts = int(svix_timestamp)
    except ValueError:
        raise VerificationError("svix-timestamp is not an integer") from None
    now = time.time() if now is None else now
    if abs(now - ts) > TOLERANCE_SECONDS:
        raise VerificationError("svix-timestamp outside tolerance window")

    try:
        secret_bytes = base64.b64decode(secret.removeprefix("whsec_"))
    except (ValueError, TypeError):
        raise VerificationError("malformed webhook secret") from None

    signed_content = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    for token in svix_signature.split():
        version, _, sig = token.partition(",")
        if version != "v1" or not sig:
            continue
        if hmac.compare_digest(sig, expected):
            return
    raise VerificationError("signature mismatch")
