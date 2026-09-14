"""Bundles one agent's static config with its own live state."""

from __future__ import annotations

from dataclasses import dataclass

from .allowlist import Allowlist
from .config import AgentConfig
from .threads import Threads


@dataclass
class AgentRuntime:
    agent: AgentConfig
    allowlist: Allowlist
    threads: Threads
