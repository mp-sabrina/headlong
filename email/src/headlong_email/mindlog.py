"""Follow an identity's root trajectory file (the mind log).

The trajectory is append-only JSONL; the bridge keeps a persisted byte
offset so restarts neither replay old steps nor miss new ones. Only
complete (newline-terminated) lines are consumed.

Copied from headlong_telegram.mindlog (the bridges are independent uv
projects) — keep fixes in sync.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator


def find_trajectory(identity_dir: Path) -> Path:
    """Locate the mind log, mirroring headlong_web.discovery.find_root_traj_dir."""
    info = {}
    info_txt = identity_dir / "info.txt"
    if info_txt.is_file():
        for line in info_txt.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip()
    traj_root = identity_dir / "trajectories"
    root_id = info.get("root_trajectory", "")
    if root_id:
        for match in sorted(traj_root.glob(f"{root_id[:8]}-*")):
            if (match / "trajectory.jsonl").is_file():
                return match / "trajectory.jsonl"
    if traj_root.is_dir():
        for candidate in sorted(traj_root.iterdir()):
            if (candidate / "trajectory.jsonl").is_file():
                return candidate / "trajectory.jsonl"
    raise SystemExit(f"headlong-email-bridge: no trajectory.jsonl under {traj_root}")


def read_new(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSONL lines appended since offset.

    Returns (steps, new_offset). Corrupt lines are skipped. If the file
    shrank (rebuilt/truncated) reading restarts from the beginning.
    """
    size = path.stat().st_size
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    with path.open("rb") as f:
        f.seek(offset)
        buf = f.read(size - offset)
    last_newline = buf.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    steps: list[dict[str, Any]] = []
    for line in buf[: last_newline + 1].splitlines():
        try:
            step = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(step, dict):
            steps.append(step)
    return steps, offset + last_newline + 1


def follow(
    path: Path,
    cursor_file: Path,
    poll_interval: float = 0.4,
    should_stop: Callable[[], bool] = lambda: False,
) -> Iterator[dict[str, Any]]:
    """Yield steps appended to the trajectory, starting at EOF (no replay)."""
    if cursor_file.is_file():
        try:
            offset = int(cursor_file.read_text().strip())
        except ValueError:
            offset = path.stat().st_size
    else:
        offset = path.stat().st_size
    while not should_stop():
        steps, new_offset = read_new(path, offset)
        if new_offset != offset:
            offset = new_offset
            cursor_file.parent.mkdir(parents=True, exist_ok=True)
            cursor_file.write_text(str(offset))
        yield from steps
        time.sleep(poll_interval)
