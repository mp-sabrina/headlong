from headlong_email.allowlist import Allowlist


def test_approve_and_check(tmp_path):
    allowlist = Allowlist(tmp_path / "allowlist.json")
    assert not allowlist.is_approved("Jane@Example.com")
    allowlist.approve("jane@example.com", "Jane")
    assert allowlist.is_approved("Jane@Example.com")  # case-insensitive


def test_persists_across_instances(tmp_path):
    path = tmp_path / "allowlist.json"
    Allowlist(path).approve("jane@example.com")
    assert Allowlist(path).is_approved("jane@example.com")


def test_pending_rate_limited(tmp_path):
    allowlist = Allowlist(tmp_path / "allowlist.json")
    assert allowlist.note_pending("bob@example.com", "Bob") is True
    assert allowlist.note_pending("bob@example.com", "Bob") is False  # within the window


def test_deny_silences_without_further_pending(tmp_path):
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.deny("bob@example.com")
    assert not allowlist.is_approved("bob@example.com")
    assert allowlist.note_pending("bob@example.com", "Bob") is False


def test_revoke(tmp_path):
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.approve("jane@example.com")
    assert allowlist.revoke("jane@example.com") is True
    assert not allowlist.is_approved("jane@example.com")
    assert allowlist.revoke("jane@example.com") is False
