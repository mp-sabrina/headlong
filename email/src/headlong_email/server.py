"""FastAPI app: the one inbound door — a Resend webhook receiver.

This is the deliberate exception to Headlong's zero-ingress bridges
(Slack dials out over Socket Mode, Telegram long-polls outbound): Resend
pushes inbound mail in over HTTP, so something has to listen. Every
request is verified against the webhook signing secret before anything
else happens — see verify.py, which is this bridge's real perimeter
against forged mail. Keeping the listener itself off the open Internet
(a Cloudflare Tunnel, not a forwarded port) is covered in email/README.md.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException, Request

from . import verify
from .config import SharedConfig
from .inbound import Inbound

log = logging.getLogger(__name__)


def build_app(shared: SharedConfig, inbound: Inbound) -> FastAPI:
    app = FastAPI(title="headlong-email-bridge")

    @app.post(shared.webhook_path)
    async def resend_webhook(request: Request) -> dict:
        body = await request.body()
        try:
            verify.verify(body, dict(request.headers), shared.webhook_secret)
        except verify.VerificationError:
            log.warning("rejected webhook: signature verification failed")
            raise HTTPException(status_code=401, detail="invalid signature")
        try:
            event = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON")
        try:
            inbound.handle_event(event)
        except Exception:
            # Log and still 200: a broken single event shouldn't make Resend
            # retry it forever, and the exception is already visible in logs.
            log.exception("failed handling event type=%r", event.get("type"))
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app
