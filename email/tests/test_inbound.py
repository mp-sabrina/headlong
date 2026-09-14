import types

import pytest

from headlong_email.allowlist import Allowlist
from headlong_email.inbound import Inbound
from headlong_email.runtime import AgentRuntime
from headlong_email.threads import Threads


class FakeApi:
    def __init__(self, emails):
        self.emails = emails  # email_id -> full email dict
        self.sent = []

    def get_received_email(self, email_id):
        return self.emails[email_id]

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return {"id": "sent-1"}


class Shared(types.SimpleNamespace):
    pass


def make_shared():
    return Shared(web_url="http://127.0.0.1:8080")


def make_agent(tmp_path, identity="aeon", address="aeon@axon.example.com", admin="admin@example.com"):
    return types.SimpleNamespace(
        identity=identity,
        identity_api_id=identity,
        from_address=address,
        admin_address=admin,
    )


def make_registry(tmp_path, *agents):
    registry = {}
    for agent in agents:
        allowlist = Allowlist(tmp_path / f"{agent.identity}-allowlist.json")
        threads = Threads(tmp_path / f"{agent.identity}-threads.json")
        registry[agent.from_address] = AgentRuntime(agent, allowlist, threads)
    return registry


def make_event(email_id="e1"):
    return {"type": "email.received", "data": {"email_id": email_id}}


@pytest.fixture
def chat_posts(monkeypatch):
    posts = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        posts.append((url, json))
        return FakeResponse()

    monkeypatch.setattr("headlong_email.inbound.httpx.post", fake_post)
    return posts


def test_routes_to_the_agent_addressed(tmp_path, chat_posts):
    aeon = make_agent(tmp_path, "aeon", "aeon@axon.example.com")
    juno = make_agent(tmp_path, "juno", "juno@axon.example.com")
    registry = make_registry(tmp_path, aeon, juno)
    for runtime in registry.values():
        runtime.allowlist.approve("bob@example.com", "Bob")
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": ["juno@axon.example.com"], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert len(chat_posts) == 1
    url, body = chat_posts[0]
    assert "/api/identities/juno/chat" in url  # went to juno, not aeon


def test_unconfigured_recipient_is_dropped(tmp_path, chat_posts):
    aeon = make_agent(tmp_path, "aeon", "aeon@axon.example.com")
    registry = make_registry(tmp_path, aeon)
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": ["nobody@axon.example.com"], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert chat_posts == []
    assert api.sent == []  # no pending notice either -- no agent owns this address


def test_unknown_sender_is_silently_pending(tmp_path, chat_posts):
    agent = make_agent(tmp_path)
    registry = make_registry(tmp_path, agent)
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": [agent.from_address], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert chat_posts == []
    assert len(api.sent) == 1
    assert api.sent[0]["to"] == [agent.admin_address]
    assert "bob@example.com" in api.sent[0]["text"]


def test_approved_sender_reaches_mind_log(tmp_path, chat_posts):
    agent = make_agent(tmp_path)
    registry = make_registry(tmp_path, agent)
    registry[agent.from_address].allowlist.approve("bob@example.com", "Bob")
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": [agent.from_address], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert len(chat_posts) == 1
    url, body = chat_posts[0]
    assert "hello" in body["content"]
    assert body["from_name"].startswith("email-bob-")
    assert registry[agent.from_address].threads.get(body["from_name"])["address"] == "bob@example.com"


def test_allowlists_are_isolated_per_agent(tmp_path, chat_posts):
    """Approved for aeon must not mean approved for juno."""
    aeon = make_agent(tmp_path, "aeon", "aeon@axon.example.com")
    juno = make_agent(tmp_path, "juno", "juno@axon.example.com")
    registry = make_registry(tmp_path, aeon, juno)
    registry[aeon.from_address].allowlist.approve("bob@example.com", "Bob")
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": [juno.from_address], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert chat_posts == []  # bob isn't approved for juno, even though he is for aeon


def test_admin_approve_command_is_intercepted(tmp_path, chat_posts):
    agent = make_agent(tmp_path)
    registry = make_registry(tmp_path, agent)
    api = FakeApi(
        {
            "e1": {
                "from": f"Admin <{agent.admin_address}>",
                "to": [agent.from_address],
                "subject": "bridge",
                "text": "/approve bob@example.com",
                "message_id": "<m1>",
            }
        }
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert chat_posts == []
    assert registry[agent.from_address].allowlist.is_approved("bob@example.com")
    assert api.sent[0]["to"] == [agent.admin_address]
    assert "approved" in api.sent[0]["text"]


def test_admin_approve_survives_mail_client_autolinking(tmp_path, chat_posts):
    """A mail client that auto-linkifies a typed address can hand us
    "addr<mailto:addr>" as the plain-text line -- the real address must
    still get approved, not the garbled token verbatim."""
    agent = make_agent(tmp_path)
    registry = make_registry(tmp_path, agent)
    api = FakeApi(
        {
            "e1": {
                "from": f"Admin <{agent.admin_address}>",
                "to": [agent.from_address],
                "subject": "bridge",
                "text": "/approve bob@example.com<mailto:bob@example.com>",
                "message_id": "<m1>",
            }
        }
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert registry[agent.from_address].allowlist.is_approved("bob@example.com")
    assert "bob@example.com<mailto:bob@example.com>" not in registry[agent.from_address].allowlist._approved


def test_admin_command_only_affects_its_own_agent(tmp_path, chat_posts):
    aeon = make_agent(tmp_path, "aeon", "aeon@axon.example.com", admin="admin@example.com")
    juno = make_agent(tmp_path, "juno", "juno@axon.example.com", admin="admin@example.com")
    registry = make_registry(tmp_path, aeon, juno)
    api = FakeApi(
        {
            "e1": {
                "from": "Admin <admin@example.com>",
                "to": [aeon.from_address],
                "subject": "bridge",
                "text": "/approve bob@example.com",
                "message_id": "<m1>",
            }
        }
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())

    assert registry[aeon.from_address].allowlist.is_approved("bob@example.com")
    assert not registry[juno.from_address].allowlist.is_approved("bob@example.com")


def test_duplicate_email_id_is_dropped(tmp_path, chat_posts):
    agent = make_agent(tmp_path)
    registry = make_registry(tmp_path, agent)
    registry[agent.from_address].allowlist.approve("bob@example.com")
    api = FakeApi(
        {"e1": {"from": "Bob <bob@example.com>", "to": [agent.from_address], "subject": "hi", "text": "hello", "message_id": "<m1>"}}
    )
    inbound = Inbound(make_shared(), api, registry)

    inbound.handle_event(make_event())
    inbound.handle_event(make_event())

    assert len(chat_posts) == 1


def test_ignores_non_received_event_types(tmp_path, chat_posts):
    registry = make_registry(tmp_path, make_agent(tmp_path))
    inbound = Inbound(make_shared(), FakeApi({}), registry)

    inbound.handle_event({"type": "email.sent", "data": {}})

    assert chat_posts == []
