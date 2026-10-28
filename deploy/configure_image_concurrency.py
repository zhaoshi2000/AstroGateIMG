"""Apply account-pool concurrency without restarting active image services.

Run on the IMG server: python3 deploy/configure_image_concurrency.py --per-account 1
Runtime settings and the source config used by future Docker builds are both updated.
"""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def configure(root, per_account):
    if per_account < 1:
        raise ValueError("per-account must be positive")
    env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    base = env["ASTROGATE_UPSTREAM_URL"].rstrip("/")
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Run this script beside the local image upstream")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def settings(values=None):
        request = urllib.request.Request(base + "/api/settings",
            data=json.dumps(values).encode() if values is not None else None,
            headers={"Authorization": "Bearer " + env["ASTROGATE_UPSTREAM_API_KEY"], "Content-Type": "application/json"})
        with opener.open(request, timeout=20) as response:
            return json.load(response)["config"]

    desired = {"image_account_concurrency": per_account, "image_parallel_generation": True}
    current = settings()
    previous = {key: current[key] for key in desired}
    config_path = root / "upstream" / "config.json"
    original = config_path.read_bytes()
    source = json.loads(original)
    backup = root / "backups" / ("before-image-concurrency-" + str(time.time_ns()))
    backup.mkdir(parents=True, mode=0o700)
    for name, content in [("config.json", original), ("runtime.json", json.dumps(previous).encode())]:
        with os.fdopen(os.open(backup / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(content)
    try:
        settings(desired)
        applied = settings()
        if any(applied.get(key) != value for key, value in desired.items()):
            raise RuntimeError("Upstream did not apply concurrency settings")
        source.update(desired)
        config_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
    except Exception:
        config_path.write_bytes(original)
        settings(previous)
        raise
    return {**desired, "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/astrogate-img"))
    parser.add_argument("--per-account", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(configure(args.root, args.per_account)))
