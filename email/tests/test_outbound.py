import threading
import types

from headlong_email import outbound
from headlong_email.allowlist import Allowlist
from headlong_email.threads import Threads


class FakeApi:
    def __init__(self):
        self.sent = []

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return {"id": "sent-1"}


def _run_with_steps(tmp_path, steps, monkeypatch, allowlist=None, threads=None):
    monkeypatch.setattr(outbound.mindlog, "find_trajectory", lambda d: tmp_path / "t.jsonl")
    monkeypatch.setattr(outbound.mindlog, "follow", lambda *a, **k: iter(steps))
    cfg = types.SimpleNamespace(
        identity="ada", identity_dir=tmp_path, state_dir=tmp_path, from_address="axon@agent.example.com"
    )
    api = FakeApi()
    allowlist = allowlist if allowlist is not None else Allowlist(tmp_path / "allowlist.json")
    threads = threads if threads is not None else Threads(tmp_path / "threads.json")
    outbound.run(cfg, api, allowlist, threads, threading.Event())
    return api


def test_recent_posts_dedupe_window():
    recent = outbound.RecentPosts(window=300)
    assert not recent.is_duplicate("email-bob-1", "hi", now=0)
    assert recent.is_duplicate("email-bob-1", "hi", now=100)
    assert not recent.is_duplicate("email-bob-1", "hi", now=500)
    assert not recent.is_duplicate("email-jane-2", "hi", now=100)


def test_sends_reply_to_known_approved_thread(tmp_path, monkeypatch):
    conv_id = "email-bob-1234567890"
    threads = Threads(tmp_path / "threads.json")
    threads.remember_inbound(conv_id, "bob@example.com", "Bob", "hello", "<m1@x.com>")
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.approve("bob@example.com")
    steps = [
        {"type": "message", "from": "ada", "to": conv_id, "source": "chat", "content": "Hi Bob!", "step_id": "s1"}
    ]

    api = _run_with_steps(tmp_path, steps, monkeypatch, allowlist=allowlist, threads=threads)

    assert len(api.sent) == 1
    sent = api.sent[0]
    assert sent["to"] == ["bob@example.com"]
    assert sent["in_reply_to"] == "<m1@x.com>"
    assert "Hi Bob!" in sent["text"]
    assert sent["subject"] == "Re: hello"


def test_drops_reply_to_unapproved_address(tmp_path, monkeypatch):
    conv_id = "email-bob-1234567890"
    threads = Threads(tmp_path / "threads.json")
    threads.remember_inbound(conv_id, "bob@example.com", "Bob", "hello", "<m1@x.com>")
    steps = [{"type": "message", "from": "ada", "to": conv_id, "source": "chat", "content": "Hi", "step_id": "s1"}]

    api = _run_with_steps(tmp_path, steps, monkeypatch, threads=threads)  # allowlist stays empty

    assert api.sent == []


def test_drops_reply_to_unknown_conversation(tmp_path, monkeypatch):
    steps = [
        {"type": "message", "from": "ada", "to": "email-nobody-000", "source": "chat", "content": "Hi", "step_id": "s1"}
    ]

    api = _run_with_steps(tmp_path, steps, monkeypatch)

    assert api.sent == []


def test_only_chat_sourced_messages_are_sent(tmp_path, monkeypatch):
    """Only bin/chat speaks for the identity: message steps without
    source:"chat" (a thinker appending raw message steps to the trajectory)
    must not reach the recipient's inbox."""
    conv_id = "email-bob-1234567890"
    threads = Threads(tmp_path / "threads.json")
    threads.remember_inbound(conv_id, "bob@example.com", "Bob", "hello", "<m1@x.com>")
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.approve("bob@example.com")
    steps = [
        {"type": "message", "from": "ada", "to": conv_id, "source": "chat", "content": "real reply", "step_id": "aaa"},
        {"type": "message", "from": "ada", "to": conv_id, "source": "responder", "content": "forged reply", "step_id": "bbb"},
        {"type": "message", "from": "ada", "to": conv_id, "content": "unstamped reply", "step_id": "ccc"},
    ]

    api = _run_with_steps(tmp_path, steps, monkeypatch, allowlist=allowlist, threads=threads)

    assert [s["text"] for s in api.sent] == ["real reply"]


def test_ignores_non_email_recipients(tmp_path, monkeypatch):
    steps = [
        {"type": "message", "from": "ada", "to": "telegram-1-1", "source": "chat", "content": "hi", "step_id": "s1"}
    ]

    api = _run_with_steps(tmp_path, steps, monkeypatch)

    assert api.sent == []
