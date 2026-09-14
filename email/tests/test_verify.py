import base64
import hashlib
import hmac
import time

import pytest

from headlong_email import verify

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef").decode()


def _sign(body: bytes, msg_id: str, ts: str, secret: str = SECRET) -> str:
    secret_bytes = base64.b64decode(secret.removeprefix("whsec_"))
    signed_content = f"{msg_id}.{ts}.".encode() + body
    sig = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_valid_signature_passes():
    body = b'{"type": "email.received"}'
    now = time.time()
    ts = str(int(now))
    headers = {
        "svix-id": "msg_1",
        "svix-timestamp": ts,
        "svix-signature": _sign(body, "msg_1", ts),
    }
    verify.verify(body, headers, SECRET, now=now)  # does not raise


def test_valid_signature_case_insensitive_headers():
    body = b"{}"
    ts = str(int(time.time()))
    headers = {
        "Svix-Id": "msg_1",
        "Svix-Timestamp": ts,
        "Svix-Signature": _sign(body, "msg_1", ts),
    }
    verify.verify(body, headers, SECRET, now=int(ts))


def test_wrong_secret_rejected():
    body = b"{}"
    ts = str(int(time.time()))
    other_secret = "whsec_" + base64.b64encode(b"fedcba9876543210").decode()
    headers = {
        "svix-id": "msg_1",
        "svix-timestamp": ts,
        "svix-signature": _sign(body, "msg_1", ts, secret=other_secret),
    }
    with pytest.raises(verify.VerificationError):
        verify.verify(body, headers, SECRET, now=int(ts))


def test_tampered_body_rejected():
    ts = str(int(time.time()))
    sig = _sign(b'{"amount": 1}', "msg_1", ts)
    headers = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": sig}
    with pytest.raises(verify.VerificationError):
        verify.verify(b'{"amount": 999}', headers, SECRET, now=int(ts))


def test_stale_timestamp_rejected():
    body = b"{}"
    old_ts = str(int(time.time()) - 3600)
    headers = {
        "svix-id": "msg_1",
        "svix-timestamp": old_ts,
        "svix-signature": _sign(body, "msg_1", old_ts),
    }
    with pytest.raises(verify.VerificationError):
        verify.verify(body, headers, SECRET)


def test_missing_headers_rejected():
    with pytest.raises(verify.VerificationError):
        verify.verify(b"{}", {}, SECRET)


def test_multiple_signature_tokens_any_match():
    body = b"{}"
    ts = str(int(time.time()))
    real = _sign(body, "msg_1", ts)
    headers = {
        "svix-id": "msg_1",
        "svix-timestamp": ts,
        "svix-signature": f"v1,bogus== {real}",
    }
    verify.verify(body, headers, SECRET, now=int(ts))
