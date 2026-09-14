"""Run the Resend email bridge for one or more Headlong identities.

Usage: headlong-email-bridge [ROOT]

ROOT is the directory the web server serves (contains .identities/);
defaults to the current directory. Configuration comes from the
environment plus, for multi-agent setups, a JSON registry file — see
config.py.
"""

import argparse
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from . import config, outbound
from .allowlist import Allowlist
from .api import Client
from .inbound import Inbound
from .runtime import AgentRuntime
from .server import build_app
from .threads import Threads


def main() -> None:
    parser = argparse.ArgumentParser(prog="headlong-email-bridge", description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Serve root (contains .identities/)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    serve_root = Path(args.root).resolve()
    if not serve_root.is_dir():
        raise SystemExit(f"Not a directory: {serve_root}")
    shared, agents = config.load(serve_root)

    api = Client(shared.resend_api_key)
    stop_event = threading.Event()
    runtimes: dict[str, AgentRuntime] = {}
    for agent in agents:
        allowlist = Allowlist(agent.state_dir / "allowlist.json")
        if not allowlist.is_approved(agent.admin_address):
            allowlist.approve(agent.admin_address, "admin")
        threads = Threads(agent.state_dir / "threads.json")
        runtimes[agent.from_address] = AgentRuntime(agent, allowlist, threads)
        outbound.start(agent, api, allowlist, threads, stop_event)
        print(
            f"headlong-email-bridge: serving {agent.identity} <{agent.from_address}> "
            f"admin={agent.admin_address}",
            file=sys.stderr,
        )

    print(
        f"headlong-email-bridge: web={shared.web_url} "
        f"listen={shared.listen_host}:{shared.listen_port}{shared.webhook_path}",
        file=sys.stderr,
    )

    inbound = Inbound(shared, api, runtimes)
    app = build_app(shared, inbound)
    try:
        uvicorn.run(app, host=shared.listen_host, port=shared.listen_port, log_level="info")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
