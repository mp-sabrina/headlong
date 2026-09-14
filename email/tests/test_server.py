import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from headlong_email.server import build_app

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef").decode()


class Cfg:
    webhook_secret = SECRET
    webhook_path = "/resend/webhook"


class RecordingInbound:
    def __init__(self):
        self.events = []

    def handle_event(self, event):
        self.events.append(event)


def _signed_headers(body: bytes):
    ts = str(int(time.time()))
    msg_id = "msg_1"
    secret_bytes = base64.b64decode(SECRET.removeprefix("whsec_"))
    signed_content = f"{msg_id}.{ts}.".encode() + body
    sig = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


def test_valid_signature_dispatches_to_inbound():
    inbound = RecordingInbound()
    client = TestClient(build_app(Cfg(), inbound))
    body = json.dumps({"type": "email.received", "data": {"email_id": "e1"}}).encode()

    resp = client.post("/resend/webhook", content=body, headers=_signed_headers(body))

    assert resp.status_code == 200
    assert inbound.events == [{"type": "email.received", "data": {"email_id": "e1"}}]


def test_bad_signature_rejected():
    inbound = RecordingInbound()
    client = TestClient(build_app(Cfg(), inbound))
    body = json.dumps({"type": "email.received"}).encode()

    resp = client.post(
        "/resend/webhook",
        content=body,
        headers={"svix-id": "x", "svix-timestamp": str(int(time.time())), "svix-signature": "v1,bogus"},
    )

    assert resp.status_code == 401
    assert inbound.events == []


def test_missing_signature_rejected():
    inbound = RecordingInbound()
    client = TestClient(build_app(Cfg(), inbound))

    resp = client.post("/resend/webhook", content=b"{}")

    assert resp.status_code == 401


def test_healthz():
    client = TestClient(build_app(Cfg(), RecordingInbound()))
    assert client.get("/healthz").status_code == 200
