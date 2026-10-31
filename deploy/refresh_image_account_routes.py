"""Refresh private image exits and bind at most five ready accounts to each exit."""

import argparse
import concurrent.futures
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


TRACE = "https://chatgpt.com/cdn-cgi/trace"
BACKEND = "https://chatgpt.com/backend-api/me"
FIRST_PORT = 17911
PROBE_PORT = 18991
PROBE_CONTROLLER = "127.0.0.1:19094"
EXCLUDED = ("香港", "港", "hong kong", "[hk]", "hk-", "-hk", "剩余流量", "套餐到期", "防失联")


def ready_accounts(accounts):
    return sorted((account for account in accounts
                   if account.get("status") not in ("禁用", "限流", "异常")
                   and int(account.get("quota") or 0) > 0),
                  key=lambda account: (str(account.get("email") or ""), str(account.get("access_token") or "")))


def route_assignments(accounts, group_count):
    if group_count < math.ceil(len(accounts) / 5):
        raise ValueError("Not enough verified proxy exits for the ready accounts")
    return [(account, f"http://127.0.0.1:{FIRST_PORT + index % group_count}")
            for index, account in enumerate(accounts)]


def eligible_node(node):
    if not isinstance(node, dict):
        return False
    name = str(node.get("name") or "")
    return bool(node.get("server") and name and not any(marker in name.lower() for marker in EXCLUDED))


def local_json(base, key, path, data=None):
    request = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                                     method="POST" if data is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        return json.load(response)


def controller_request(controller, secret, path, data=None):
    request = urllib.request.Request("http://" + controller + path,
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"},
                                     method="PUT" if data is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return response.read()


def private_write(path, content):
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def subscription_nodes(root, current):
    nodes = {}
    for node in current.get("proxies") or []:
        if eligible_node(node):
            nodes[node["name"]] = node
    for path in sorted((root / "proxy-runtime" / "subscriptions").glob("*.txt")):
        address = path.read_text().strip()
        if not address:
            continue
        request = urllib.request.Request(address, headers={"User-Agent": "clash.meta/1.19.0"})
        for proxy in ({}, {"https": "http://127.0.0.1:17891"}):
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy))
                with opener.open(request, timeout=20) as response:
                    document = yaml.safe_load(response.read(2 * 1024 * 1024))
                for node in document.get("proxies") or []:
                    if eligible_node(node):
                        nodes[node["name"]] = node
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, AttributeError, yaml.YAMLError):
                continue
    return list(nodes.values())


def resolved_hosts(nodes, previous):
    hosts = dict(previous)
    for name in {str(node.get("server") or "") for node in nodes}:
        if not name:
            continue
        try:
            ipaddress.ip_address(name)
            continue
        except ValueError:
            pass
        addresses = []
        for resolver in ("223.5.5.5", "119.29.29.29"):
            result = subprocess.run(["dig", "+time=2", "+tries=2", "+short", "A", name, "@" + resolver],
                                    capture_output=True, text=True, timeout=7)
            for line in result.stdout.splitlines():
                try:
                    addresses.append(str(ipaddress.IPv4Address(line.strip())))
                except ipaddress.AddressValueError:
                    pass
            if addresses:
                break
        if addresses:
            hosts[name] = sorted(set(addresses))
    return hosts


def backend_status(port):
    result = subprocess.run(["curl", "-x", f"http://127.0.0.1:{port}", "-sS", "-o", "/dev/null",
                             "-w", "%{http_code}", "--max-time", "10", BACKEND],
                            capture_output=True, text=True, timeout=12)
    return result.stdout.strip() if result.returncode == 0 else "unreachable"


def rank_nodes(root, mihomo, nodes, hosts):
    with tempfile.TemporaryDirectory(prefix="image-route-probe-", dir=root / "proxy-runtime") as directory:
        folder = Path(directory)
        config = {"mixed-port": PROBE_PORT, "allow-lan": False, "bind-address": "127.0.0.1",
                  "mode": "rule", "log-level": "warning", "ipv6": False,
                  "external-controller": PROBE_CONTROLLER, "secret": "", "hosts": hosts,
                  "proxies": nodes, "proxy-groups": [{"name": "PROXY", "type": "select",
                                                      "proxies": [node["name"] for node in nodes]}],
                  "rules": ["MATCH,PROXY"]}
        private_write(folder / "config.yaml", yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode())
        checked = subprocess.run([str(mihomo), "-t", "-d", str(folder)], capture_output=True, timeout=30)
        if checked.returncode:
            raise RuntimeError("Proxy candidate configuration failed validation")
        with open(os.devnull, "wb") as output:
            process = subprocess.Popen([str(mihomo), "-d", str(folder)], stdout=output, stderr=output)
            try:
                for _ in range(30):
                    try:
                        controller_request(PROBE_CONTROLLER, "", "/proxies")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Proxy probe did not start")

                def delay(name):
                    path = "/proxies/" + urllib.parse.quote(name, safe="") + "/delay?url=" + urllib.parse.quote(TRACE, safe="") + "&timeout=9000"
                    try:
                        return json.loads(controller_request(PROBE_CONTROLLER, "", path))["delay"], name
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError):
                        return None, name

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    candidates = sorted((latency, name) for latency, name in pool.map(delay, [node["name"] for node in nodes])
                                        if isinstance(latency, int) and latency > 0)
                verified = []
                for latency, name in candidates:
                    controller_request(PROBE_CONTROLLER, "", "/proxies/PROXY", {"name": name})
                    if backend_status(PROBE_PORT) == "401":
                        verified.append((latency, name))
                return verified
            finally:
                process.terminate()
                process.wait(timeout=5)


def production_config(current, nodes, hosts, verified, group_count):
    names = [name for _, name in verified]
    by_name = {node["name"]: node for node in nodes}
    selected = names[:group_count]
    reserve = names[group_count:]
    config = dict(current)
    config["hosts"] = hosts
    config["proxies"] = [by_name[name] for name in names]
    config["proxy-groups"] = [{"name": "PROXY", "type": "fallback", "proxies": names,
                               "url": TRACE, "interval": 90, "lazy": False}]
    config["listeners"] = []
    for index, name in enumerate(selected):
        alternate = [selected[(index + offset) % len(selected)] for offset in range(1, min(4, len(selected)))]
        choices = list(dict.fromkeys([name, *alternate, *reserve[:2]]))
        group = f"IMG-{index + 1:02d}"
        config["proxy-groups"].append({"name": group, "type": "fallback", "proxies": choices,
                                       "url": TRACE, "interval": 90, "lazy": False})
        config["listeners"].append({"name": group.lower(), "type": "mixed", "listen": "127.0.0.1",
                                    "port": FIRST_PORT + index, "proxy": group})
    return config


def deploy(root, mihomo):
    env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    upstream = env["ASTROGATE_UPSTREAM_URL"].strip().rstrip("/")
    if urllib.parse.urlsplit(upstream).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Image upstream must be local")
    key = env["ASTROGATE_UPSTREAM_API_KEY"].strip()
    accounts = local_json(upstream, key, "/api/accounts")["items"]
    ready = ready_accounts(accounts)
    if any(int(account.get("image_inflight") or 0) for account in accounts):
        return {"status": "busy", "ready_accounts": len(ready)}
    settings = local_json(upstream, key, "/api/settings")["config"]
    if int(settings.get("image_account_concurrency") or 0) != 1:
        raise ValueError("Set image_account_concurrency=1 before routing accounts")
    path = root / "proxy-runtime" / "config.yaml"
    original = path.read_bytes()
    current = yaml.safe_load(original)
    nodes = subscription_nodes(root, current)
    hosts = resolved_hosts(nodes, current.get("hosts") or {})
    verified = rank_nodes(root, mihomo, nodes, hosts)
    old_groups = len([item for item in current.get("listeners") or [] if str(item.get("name") or "").startswith("img-")])
    if old_groups:
        previous = [group["proxies"][0] for group in current.get("proxy-groups") or []
                    if str(group.get("name") or "").startswith("IMG-")]
        previous += [node["name"] for node in current.get("proxies") or []]
        latency_by_name = dict((name, latency) for latency, name in verified)
        stable = list(dict.fromkeys(name for name in previous if name in latency_by_name and latency_by_name[name] <= 2500))
        verified = [(latency_by_name[name], name) for name in stable] + [entry for entry in verified if entry[1] not in stable]
    group_count = max(old_groups, math.ceil(len(ready) / 5))
    if len(verified) < group_count:
        return {"status": "insufficient", "ready_accounts": len(ready), "verified_exits": len(verified),
                "needed_exits": group_count}
    assigned = route_assignments(ready, group_count)
    proposed = production_config(current, nodes, hosts, verified, group_count)
    if proposed == current and all(account.get("proxy") == proxy for account, proxy in assigned):
        return {"status": "unchanged", "ready_accounts": len(ready), "verified_exits": len(verified),
                "routed_exits": group_count}
    backup = root / "backups" / ("image-routes-" + datetime.now(timezone.utc).strftime("%Y%m%d"))
    backup.mkdir(parents=True, mode=0o700)
    if not (backup / "config.yaml").exists():
        private_write(backup / "config.yaml", original)
        private_write(backup / "account-proxies.json", json.dumps(
            [{"access_token": account["access_token"], "proxy": account.get("proxy") or ""} for account in ready]).encode())
    candidate = path.with_suffix(".yaml.next")
    private_write(candidate, yaml.safe_dump(proposed, allow_unicode=True, sort_keys=False).encode())
    checked = subprocess.run([str(mihomo), "-t", "-d", str(path.parent), "-f", str(candidate)],
                             capture_output=True, timeout=30)
    if checked.returncode:
        candidate.unlink(missing_ok=True)
        raise RuntimeError("Production proxy configuration failed validation")
    controller = current["external-controller"]
    secret = str(current.get("secret") or "")
    changed = []
    try:
        controller_request(controller, secret, "/configs", {"path": str(candidate)})
        for index in (0, group_count - 1):
            if backend_status(FIRST_PORT + index) != "401":
                raise RuntimeError("Proxy listener did not pass the ChatGPT route check")
        for account, proxy in assigned:
            if account.get("proxy") == proxy:
                continue
            local_json(upstream, key, "/api/accounts/update", {"access_token": account["access_token"], "proxy": proxy})
            changed.append(account)
        candidate.replace(path)
    except Exception:
        for account in changed:
            try:
                local_json(upstream, key, "/api/accounts/update",
                           {"access_token": account["access_token"], "proxy": account.get("proxy") or ""})
            except Exception:
                pass
        controller_request(controller, secret, "/configs", {"path": str(path)})
        candidate.unlink(missing_ok=True)
        raise
    return {"status": "updated", "ready_accounts": len(ready), "verified_exits": len(verified),
            "routed_exits": group_count, "accounts_updated": len(changed), "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/astrogate-img"))
    parser.add_argument("--mihomo", type=Path, default=Path("/srv/astrogate/.local/bin/mihomo"))
    args = parser.parse_args()
    try:
        print(json.dumps(deploy(args.root, args.mihomo)))
    except Exception as error:
        print(json.dumps({"status": "failed", "reason": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
