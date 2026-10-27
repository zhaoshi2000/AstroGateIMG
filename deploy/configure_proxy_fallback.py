#!/usr/bin/env python3
"""Add only approved subscription regions after the dedicated proxy nodes.

The subscription and destination configuration are private runtime files. This
script prints only node counts, never subscription URLs or proxy credentials.
"""

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--subscription", type=Path, required=True)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    subscription = yaml.safe_load(args.subscription.read_text(encoding="utf-8"))
    primary = [
        node for node in config["proxies"] if not node["name"].startswith("B · ")
    ]

    allowed = []
    for region in ("新加坡", "日本", "美国"):
        for node in subscription.get("proxies", []):
            name = node.get("name", "")
            if region not in name:
                continue
            selected = dict(node)
            selected["name"] = "B · " + name
            allowed.append(selected)

    if not primary or not allowed:
        raise SystemExit("Primary nodes or approved subscription nodes are missing")
    config["proxies"] = primary + allowed
    group = next(g for g in config["proxy-groups"] if g["name"] == "PROXY")
    group["type"] = "fallback"
    group["proxies"] = [node["name"] for node in config["proxies"]]
    group["url"] = "https://chatgpt.com/cdn-cgi/trace"
    group["interval"] = 180

    temp = args.config.with_suffix(".yaml.next")
    temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(args.config)
    print(f"Configured {len(primary)} primary and {len(allowed)} approved B nodes")


if __name__ == "__main__":
    main()
