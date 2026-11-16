"""Conservatively recover the local image upstream after sustained TCP failure.

Run once per minute from astrogate-img-upstream-guard.timer. This deliberately
does not use Docker's health state: a slow but live image generation is not a
reason to restart its container.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

try:
    import fcntl
except ImportError:  # Let pure decision and task checks run in Windows tests.
    fcntl = None


CONTAINER = "astrogate-img-upstream-1"
HOST = "127.0.0.1"
PORT = 21873
FAILURE_SECONDS = 180
# The longest bridge request is 14 minutes; allow extra time for slow cleanup.
ACTIVE_WINDOW_SECONDS = 30 * 60
DEFAULT_ROOT = Path("/opt/astrogate-img")
DEFAULT_STATE = Path("/var/lib/astrogate-img-upstream-guard/state.json")


class SafetyCheckFailed(RuntimeError):
    """Do not restart when in-flight work cannot be ruled out."""


def docker_state() -> dict:
    command = ["docker", "inspect", "--format", "{{.Id}}|{{.State.Running}}|{{.State.StartedAt}}", CONTAINER]
    result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise RuntimeError("image upstream container could not be inspected")
    parts = result.stdout.strip().split("|", 2)
    if len(parts) != 3 or parts[1] not in ("true", "false") or not parts[0]:
        raise RuntimeError("image upstream container state is invalid")
    return {"instance": parts[0] + ":" + parts[2], "running": parts[1] == "true"}


def tcp_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except (OSError, TimeoutError):
        return False


def _fresh(timestamp: object, now: float) -> bool:
    try:
        value = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise SafetyCheckFailed("unfinished image task has no valid start time") from exc
    if not math.isfinite(value):
        raise SafetyCheckFailed("unfinished image task has no valid start time")
    return now - value < ACTIVE_WINDOW_SECONDS


def _iso_timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise SafetyCheckFailed("unfinished image task has no valid start time")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SafetyCheckFailed("unfinished image task has no valid start time") from exc
    if stamp.tzinfo is None:
        # The upstream task store uses local naive timestamps.
        return stamp.timestamp()
    return stamp.astimezone(timezone.utc).timestamp()


def _recent_sqlite_rows(path: Path, query: str, now: float, *, required: bool) -> bool:
    if not path.exists():
        if required:
            raise SafetyCheckFailed("image customer job database is missing")
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            rows = db.execute(query).fetchall()
        return any(_fresh(_iso_timestamp(row[0]) if isinstance(row[0], str) else row[0], now)
                   for row in rows)
    except sqlite3.Error as exc:
        raise SafetyCheckFailed("image job database could not be read") from exc


def established_requests() -> bool:
    """Check accepted connections, including requests made outside the IMG app."""
    port = f"{PORT:04X}"
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = path.read_text().splitlines()
        except OSError as exc:
            raise SafetyCheckFailed("active TCP connections could not be checked") from exc
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4:
                raise SafetyCheckFailed("active TCP connection table is invalid")
            if fields[1].rsplit(":", 1)[-1].upper() == port and fields[3] == "01":
                return True
    return False


def active_image_work(root: Path, now: float) -> bool:
    if established_requests():
        return True
    if _recent_sqlite_rows(root / "data/astrogate.db",
                           "SELECT created_at FROM generation_jobs WHERE status = 'pending'", now, required=True):
        return True
    if _recent_sqlite_rows(root / "data/gateway-bridge.sqlite3",
                           "SELECT created FROM image_jobs WHERE status = 'pending'", now, required=False):
        return True
    tasks_path = root / "upstream-data/image_tasks.json"
    if tasks_path.exists():
        try:
            payload = json.loads(tasks_path.read_text(encoding="utf-8"))
            tasks = payload.get("tasks") if isinstance(payload, dict) else payload
            if not isinstance(tasks, list):
                raise ValueError("invalid task list")
            for task in tasks:
                if not isinstance(task, dict):
                    raise ValueError("invalid task entry")
                if task.get("status") in ("queued", "running"):
                    stamp = task.get("updated_ts") or task.get("started_ts") or task.get("created_ts")
                    if _fresh(stamp if stamp is not None else _iso_timestamp(task.get("created_at")), now):
                        return True
        except (OSError, ValueError, TypeError) as exc:
            raise SafetyCheckFailed("upstream image task state could not be read") from exc
    return False


def decide(state: dict, container: dict, reachable: bool, now: float, active: bool = False) -> tuple[str, dict]:
    """Pure state transition; active is consulted only after the grace period."""
    if not container["running"]:
        return "start", {"instance": container["instance"], "first_failure": None}
    instance = container["instance"]
    if reachable:
        return "healthy", {"instance": instance, "first_failure": None}
    first = state.get("first_failure") if state.get("instance") == instance else None
    if not isinstance(first, (int, float)) or not math.isfinite(first) or first > now:
        first = now
    updated = {"instance": instance, "first_failure": first}
    if now - first < FAILURE_SECONDS:
        return "waiting", updated
    return ("deferred" if active else "restart"), updated


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def write_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.write("\n")
    os.replace(temporary, path)


def docker_action(action: str) -> None:
    command = ["docker", "start" if action == "start" else "restart"]
    if action == "restart":
        command.extend(["--time", "30"])
    command.append(CONTAINER)
    result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise RuntimeError("image upstream container recovery failed")


def run_once(root: Path, state_file: Path, now: float | None = None) -> str:
    if fcntl is None:
        raise RuntimeError("image upstream guard requires Linux")
    now = time.time() if now is None else now
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_file.parent / "guard.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state(state_file)
        container = docker_state()
        action, updated = decide(state, container, tcp_open() if container["running"] else False, now)
        if action == "restart":
            try:
                busy = active_image_work(root, now)
            except SafetyCheckFailed:
                busy = True
            action, updated = decide(state, container, False, now, busy)
            if action == "restart":
                # A concurrent operator action or a recovered listener cancels recovery.
                if docker_state() != container or tcp_open():
                    action, updated = "changed", {"instance": container["instance"], "first_failure": None}
        if action in ("start", "restart"):
            docker_action(action)
            updated["first_failure"] = None
        write_state(state_file, updated)
        return action


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    print("image upstream guard: " + run_once(args.root, args.state_file))


if __name__ == "__main__":
    main()
