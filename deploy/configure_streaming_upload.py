"""Stream image uploads through the IMG HTTPS nginx route instead of buffering.

Run on the edge server: python3 deploy/configure_streaming_upload.py --site PATH
"""
import argparse
import os
import re
import subprocess
import time
from pathlib import Path

DOMAIN = "img.astrogate.sbs"
UPSTREAM = "http://127.0.0.1:43922"
SERVERS = re.compile(r"^(?P<indent>[ \t]*)server[ \t]*\{[^\n]*\n.*?^(?P=indent)\}[ \t]*(?:#[^\n]*)?$", re.MULTILINE | re.DOTALL)
LOCATIONS = re.compile(r"^(?P<indent>[ \t]+)location[ \t]+/[ \t]*\{[ \t]*\n(?P<body>.*?)^(?P=indent)\}[ \t]*(?:#[^\n]*)?$", re.MULTILINE | re.DOTALL)


def patch_source(source):
    original = source
    newline = "\r\n" if "\r\n" in source else "\n"
    if newline == "\r\n" and source.count("\n") != source.count("\r\n"):
        raise ValueError("Mixed config line endings; no config changed")
    source = source.replace("\r\n", "\n")
    matches = [block for block in SERVERS.finditer(source)
               if [name.strip() for name in re.findall(r"^[ \t]*server_name[ \t]+([^;]+);", block.group(), re.MULTILINE)] == [DOMAIN]
               and re.search(r"^[ \t]*listen[ \t]+[^;]*\bssl\b[^;]*;", block.group(), re.MULTILINE)]
    if len(matches) != 1:
        raise ValueError("Expected exactly one IMG HTTPS server; no config changed")
    server = matches[0]
    locations = list(LOCATIONS.finditer(server.group()))
    if len(locations) != 1:
        raise ValueError("Expected exactly one IMG root location; no config changed")
    location = locations[0]
    body = location.group("body")
    if "{" in body or "}" in body:
        raise ValueError("Unexpected nested IMG location; no config changed")
    passes = re.findall(r"^[ \t]*proxy_pass[ \t]+([^;]+);", body, re.MULTILINE)
    versions = re.findall(r"^[ \t]*proxy_http_version[ \t]+([^;]+);", body, re.MULTILINE)
    if passes != [UPSTREAM] or versions != ["1.1"]:
        raise ValueError("Expected IMG tunnel upstream with HTTP/1.1; no config changed")
    buffering = re.findall(r"^[ \t]*proxy_request_buffering[ \t]+([^;]+);", body, re.MULTILINE)
    if buffering == ["off"]:
        return original
    if buffering:
        raise ValueError("Unexpected request buffering directive; no config changed")
    anchors = list(re.finditer(r"^(?P<indent>[ \t]+)proxy_buffering[ \t]+off[ \t]*;[ \t]*(?:#[^\n]*)?$", body, re.MULTILINE))
    if len(anchors) != 1:
        raise ValueError("Expected one disabled response buffering directive; no config changed")
    anchor = anchors[0]
    insertion = anchor.end()
    body = body[:insertion] + "\n" + anchor.group("indent") + "proxy_request_buffering off;" + body[insertion:]
    start = server.start() + location.start("body")
    end = server.start() + location.end("body")
    return (source[:start] + body + source[end:]).replace("\n", newline)


def configure(site):
    site = site.resolve(strict=True)
    original = site.read_bytes()
    updated = patch_source(original.decode("utf-8")).encode("utf-8")
    if updated == original:
        return {"changed": False}
    backup = site.with_name("." + site.name + ".before-streaming-upload-" + str(time.time_ns()))
    with os.fdopen(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(original)
    try:
        site.write_bytes(updated)
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    except (OSError, subprocess.CalledProcessError):
        site.write_bytes(original)
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        raise
    return {"changed": True, "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    print(configure(args.site))
