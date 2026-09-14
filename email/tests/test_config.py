import json

import pytest

from headlong_email import config


def _make_identity(root, name="ada"):
    identity_dir = root / ".identities" / name
    identity_dir.mkdir(parents=True)
    (identity_dir / "info.txt").write_text("root_trajectory=\n")
    return identity_dir


def _base_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_x")


def test_load_requires_resend_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="RESEND_API_KEY"):
        config.load(tmp_path)


def test_load_requires_webhook_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    with pytest.raises(SystemExit, match="RESEND_WEBHOOK_SECRET"):
        config.load(tmp_path)


def test_load_requires_some_agent_config(tmp_path, monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("AXON_EMAIL_ADDRESS", raising=False)
    with pytest.raises(SystemExit, match="no agents configured"):
        config.load(tmp_path)


def test_single_agent_from_env_vars(tmp_path, monkeypatch):
    _make_identity(tmp_path)
    (tmp_path / ".identities" / "default").symlink_to("ada")
    _base_env(monkeypatch)
    monkeypatch.setenv("AXON_EMAIL_ADDRESS", "Axon@Agent.Example.com")
    monkeypatch.setenv("HEADLONG_EMAIL_ADMIN", "Sabrina@Example.com")
    shared, agents = config.load(tmp_path)
    assert len(agents) == 1
    agent = agents[0]
    assert agent.identity == "ada"
    assert agent.from_address == "axon@agent.example.com"
    assert agent.admin_address == "sabrina@example.com"
    assert agent.identity_api_id == ".identities~ada"
    assert agent.state_dir.is_dir()
    assert shared.web_url == "http://127.0.0.1:8080"


def test_multi_agent_registry_file(tmp_path, monkeypatch):
    _make_identity(tmp_path, "aeon")
    _make_identity(tmp_path, "juno")
    registry = tmp_path / ".headlong-email-agents.json"
    registry.write_text(
        json.dumps(
            [
                {"identity": "aeon", "address": "aeon@axon.example.com", "admin": "sabrina@example.com"},
                {"identity": "juno", "address": "juno@axon.example.com"},
            ]
        )
    )
    _base_env(monkeypatch)
    monkeypatch.setenv("HEADLONG_EMAIL_ADMIN", "default-admin@example.com")
    shared, agents = config.load(tmp_path)
    assert [a.identity for a in agents] == ["aeon", "juno"]
    assert agents[0].admin_address == "sabrina@example.com"  # per-agent override
    assert agents[1].admin_address == "default-admin@example.com"  # shared fallback


def test_multi_agent_registry_file_custom_path(tmp_path, monkeypatch):
    _make_identity(tmp_path, "aeon")
    registry = tmp_path / "somewhere-else.json"
    registry.write_text(json.dumps([{"identity": "aeon", "address": "aeon@x.com", "admin": "a@x.com"}]))
    _base_env(monkeypatch)
    monkeypatch.setenv("HEADLONG_EMAIL_AGENTS_FILE", str(registry))
    shared, agents = config.load(tmp_path)
    assert len(agents) == 1


def test_duplicate_address_rejected(tmp_path, monkeypatch):
    _make_identity(tmp_path, "aeon")
    _make_identity(tmp_path, "juno")
    registry = tmp_path / ".headlong-email-agents.json"
    registry.write_text(
        json.dumps(
            [
                {"identity": "aeon", "address": "same@x.com", "admin": "a@x.com"},
                {"identity": "juno", "address": "Same@X.com", "admin": "a@x.com"},
            ]
        )
    )
    _base_env(monkeypatch)
    with pytest.raises(SystemExit, match="more than one agent"):
        config.load(tmp_path)


def test_agent_missing_admin_rejected(tmp_path, monkeypatch):
    _make_identity(tmp_path, "aeon")
    registry = tmp_path / ".headlong-email-agents.json"
    registry.write_text(json.dumps([{"identity": "aeon", "address": "aeon@x.com"}]))
    _base_env(monkeypatch)
    monkeypatch.delenv("HEADLONG_EMAIL_ADMIN", raising=False)
    with pytest.raises(SystemExit, match="no admin address"):
        config.load(tmp_path)


def test_unknown_identity_rejected(tmp_path, monkeypatch):
    registry = tmp_path / ".headlong-email-agents.json"
    registry.write_text(json.dumps([{"identity": "ghost", "address": "ghost@x.com", "admin": "a@x.com"}]))
    _base_env(monkeypatch)
    with pytest.raises(SystemExit, match="'ghost' not found"):
        config.load(tmp_path)
