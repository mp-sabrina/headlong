"""Bridge configuration: one SharedConfig plus a list of AgentConfigs.

One bridge process — one Resend webhook, one HTTP listener, one Resend
API key — can serve several identities at once, each with its own email
address, admin, and allowlist. Inbound routing picks the right agent by
matching the webhook's `to`/`received_for` address; outbound runs one
trajectory-following thread per agent. See email/README.md for the
agents-registry file format.

A single `full_access` key is used for both sending and reading received
mail: reading received-email bodies appears to require `full_access` (a
`sending_access` key can be scoped to send-only + one domain, but doesn't
have read access), which makes a second, narrower send-only key pointless
— the one key held by the bridge is already the most-privileged one
either way. See "Keeping the Resend key away from the agent" in
email/README.md for what that does and doesn't protect against.

All settings come from the process environment plus, for multi-agent
setups, a JSON registry file. In production this should be loaded the way
the Telegram bridge isolates its token — a root-owned env file that only
the bridge's own service user and systemd read, kept out of the agent's
own environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SharedConfig:
    resend_api_key: str
    webhook_secret: str
    web_url: str
    listen_host: str
    listen_port: int
    webhook_path: str


@dataclass(frozen=True)
class AgentConfig:
    serve_root: Path
    identity: str
    identity_dir: Path
    from_address: str  # this agent's own address, e.g. aeon@axon.example.com
    admin_address: str  # approves/denies senders; auto-approved on first start
    state_dir: Path

    @property
    def identity_api_id(self) -> str:
        """Identity id for the web API: root-relative path with / -> ~."""
        rel = self.identity_dir.relative_to(self.serve_root)
        return str(rel).replace("/", "~")


def _default_identity(serve_root: Path) -> str:
    """The identity the `default` symlink points at, as `persona` resolves it.

    The immediate link target, not the end of the chain: an identity dir may
    itself be a symlink elsewhere, and its name here is the one `identity
    default` recorded.
    """
    for base in (".identities", "identities"):
        link = serve_root / base / "default"
        if link.is_symlink():
            name = link.readlink().name
            if not (serve_root / base / name).is_dir():
                raise SystemExit(
                    f"headlong-email-bridge: the default identity link {link} "
                    f"points at '{name}', which is not an identity under "
                    f"{serve_root / base}. Repoint it with: identity default {name}"
                )
            return name
    raise SystemExit(
        "headlong-email-bridge: no identity given and no default set "
        f"under {serve_root} (looked for .identities/default and "
        "identities/default). Set HEADLONG_EMAIL_IDENTITY, or point the "
        "default at one with: identity default <name>"
    )


def _find_identity_dir(serve_root: Path, name: str) -> Path:
    for base in (".identities", "identities"):
        candidate = serve_root / base / name
        if (candidate / "info.txt").is_file():
            return candidate
    raise SystemExit(
        f"headlong-email-bridge: identity '{name}' not found under {serve_root} "
        "(looked in .identities/ and identities/). Create it with: identity new "
        + name
    )


def _state_dir_for(identity_dir: Path) -> Path:
    state_dir = identity_dir / "run" / "email-bridge"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _load_agent_specs(serve_root: Path) -> list[dict]:
    """A JSON list of {identity, address, admin?} — one entry per identity.

    `admin` may be omitted and filled in from HEADLONG_EMAIL_ADMIN. With no
    registry file and no override path set, falls back to a single agent
    built entirely from AXON_EMAIL_ADDRESS/HEADLONG_EMAIL_IDENTITY, so an
    existing single-identity setup keeps working unchanged.
    """
    override = os.environ.get("HEADLONG_EMAIL_AGENTS_FILE")
    path = Path(override) if override else serve_root / ".headlong-email-agents.json"
    if path.is_file():
        try:
            specs = json.loads(path.read_text())
        except ValueError as exc:
            raise SystemExit(f"headlong-email-bridge: {path} is not valid JSON: {exc}") from None
        if not isinstance(specs, list) or not specs:
            raise SystemExit(f"headlong-email-bridge: {path} must be a non-empty JSON list of agents")
        return specs

    address = os.environ.get("AXON_EMAIL_ADDRESS", "")
    if not address:
        raise SystemExit(
            "headlong-email-bridge: no agents configured. Either create "
            f"{path} (a JSON list of [{{\"identity\": ..., \"address\": ..., "
            '"admin": ...}}]) or set AXON_EMAIL_ADDRESS (and optionally '
            "HEADLONG_EMAIL_IDENTITY/HEADLONG_EMAIL_ADMIN) for a single identity."
        )
    return [
        {
            "identity": os.environ.get("HEADLONG_EMAIL_IDENTITY") or _default_identity(serve_root),
            "address": address,
            "admin": os.environ.get("HEADLONG_EMAIL_ADMIN", ""),
        }
    ]


def load(serve_root: Path) -> tuple[SharedConfig, list[AgentConfig]]:
    resend_api_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_api_key:
        raise SystemExit(
            "headlong-email-bridge: RESEND_API_KEY is required (a Resend "
            "full_access key — see email/README.md for what that does and "
            "doesn't protect against)"
        )
    webhook_secret = os.environ.get("RESEND_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise SystemExit(
            "headlong-email-bridge: RESEND_WEBHOOK_SECRET is required "
            "(the whsec_... signing secret from the webhook's page in the "
            "Resend dashboard)"
        )
    default_admin = os.environ.get("HEADLONG_EMAIL_ADMIN", "").strip().lower()

    agents: list[AgentConfig] = []
    seen_addresses: set[str] = set()
    for spec in _load_agent_specs(serve_root):
        identity = str(spec.get("identity") or "").strip()
        address = str(spec.get("address") or "").strip().lower()
        admin = str(spec.get("admin") or default_admin).strip().lower()
        if not identity or not address or "@" not in address:
            raise SystemExit(
                f"headlong-email-bridge: bad agent entry {spec!r} "
                "(needs a non-empty 'identity' and 'address')"
            )
        if not admin or "@" not in admin:
            raise SystemExit(
                f"headlong-email-bridge: agent '{identity}' has no admin address "
                "(set 'admin' on its entry, or HEADLONG_EMAIL_ADMIN as a shared default)"
            )
        if address in seen_addresses:
            raise SystemExit(
                f"headlong-email-bridge: address {address} is configured for more "
                "than one agent — each identity needs its own address"
            )
        seen_addresses.add(address)
        identity_dir = _find_identity_dir(serve_root, identity)
        agents.append(
            AgentConfig(
                serve_root=serve_root,
                identity=identity,
                identity_dir=identity_dir,
                from_address=address,
                admin_address=admin,
                state_dir=_state_dir_for(identity_dir),
            )
        )

    shared = SharedConfig(
        resend_api_key=resend_api_key,
        webhook_secret=webhook_secret,
        web_url=(os.environ.get("HEADLONG_WEB_URL") or "http://127.0.0.1:8080").rstrip("/"),
        listen_host=os.environ.get("HEADLONG_EMAIL_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.environ.get("HEADLONG_EMAIL_LISTEN_PORT", "8090")),
        webhook_path=os.environ.get("HEADLONG_EMAIL_WEBHOOK_PATH", "/resend/webhook"),
    )
    return shared, agents
