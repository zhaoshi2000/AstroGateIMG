"""Set the image stream and polling timeout in the running and source config."""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def configure(root, seconds):
    if seconds < 1:
        raise ValueError("Timeout must be positive")
    env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    base = env["ASTROGATE_UPSTREAM_URL"].rstrip("/")
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Run this script beside the local image upstream")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def settings(values=None):
        request = urllib.request.Request(base + "/api/settings",
            data=json.dumps(values).encode() if values is not None else None,
            headers={"Authorization": "Bearer " + env["ASTROGATE_UPSTREAM_API_KEY"], "Content-Type": "application/json"})
        with opener.open(request, timeout=20) as response:
            return json.load(response)["config"]

    current = settings()["image_poll_timeout_secs"]
    config_path = root / "upstream" / "config.json"
    original = config_path.read_bytes()
    source = json.loads(original)
    if current == seconds and source.get("image_poll_timeout_secs") == seconds:
        return {"changed": False, "image_poll_timeout_secs": seconds}
    backup = root / "backups" / ("before-image-timeout-" + str(time.time_ns()))
    backup.mkdir(parents=True, mode=0o700)
    for name, content in [("config.json", original), ("runtime.json", json.dumps({"image_poll_timeout_secs": current}).encode())]:
        with os.fdopen(os.open(backup / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(content)
    try:
        settings({"image_poll_timeout_secs": seconds})
        if settings()["image_poll_timeout_secs"] != seconds:
            raise RuntimeError("Upstream did not apply image timeout")
        source["image_poll_timeout_secs"] = seconds
        config_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        config_path.write_bytes(original)
        settings({"image_poll_timeout_secs": current})
        raise
    return {"changed": True, "image_poll_timeout_secs": seconds, "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/astrogate-img"))
    parser.add_argument("--seconds", type=int, default=720)
    args = parser.parse_args()
    print(json.dumps(configure(args.root, args.seconds)))
