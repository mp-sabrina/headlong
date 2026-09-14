from headlong_email.threads import Threads


def test_remember_and_get(tmp_path):
    threads = Threads(tmp_path / "threads.json")
    threads.remember_inbound("email-jane-abc", "jane@example.com", "Jane", "Hi", "<m1@x.com>")
    entry = threads.get("email-jane-abc")
    assert entry["address"] == "jane@example.com"
    assert entry["name"] == "Jane"
    assert entry["subject"] == "Hi"
    assert entry["last_message_id"] == "<m1@x.com>"
    assert entry["references"] == ["<m1@x.com>"]


def test_unknown_conversation_returns_none(tmp_path):
    threads = Threads(tmp_path / "threads.json")
    assert threads.get("email-nope-000") is None


def test_references_accumulate_and_dedupe(tmp_path):
    threads = Threads(tmp_path / "threads.json")
    threads.remember_inbound("c1", "a@x.com", "A", "Hi", "<m1@x.com>")
    threads.remember_inbound("c1", "a@x.com", "A", "Hi", "<m2@x.com>")
    threads.remember_inbound("c1", "a@x.com", "A", "Hi", "<m1@x.com>")  # duplicate
    entry = threads.get("c1")
    assert entry["references"] == ["<m1@x.com>", "<m2@x.com>"]
    assert entry["last_message_id"] == "<m1@x.com>"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "threads.json"
    Threads(path).remember_inbound("c1", "a@x.com", "A", "Hi", "<m1@x.com>")
    entry = Threads(path).get("c1")
    assert entry["address"] == "a@x.com"
