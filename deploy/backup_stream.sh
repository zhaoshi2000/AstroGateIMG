#!/usr/bin/env bash
set -euo pipefail

# Streams a consistent private backup to stdout. Intended for a restricted
# SSH key; keep stdout exclusively for the tar stream.
root=/opt/astrogate-img
required_bytes=$(du -sb "$root/app" "$root/upstream" "$root/data" "$root/upstream-data" "$root/proxy-runtime" | awk '{total += $1} END {print total}')
available_bytes=$(df -B1 --output=avail /dev/shm | tail -n 1)
if (( available_bytes < required_bytes * 2 )); then
    echo 'Insufficient tmpfs space for a consistent image backup' >&2
    exit 1
fi

# The public host has little free disk space. Keep the consistent snapshot in
# tmpfs, and stream the compressed archive directly to the backup machine.
staging=$(mktemp -d /dev/shm/astrogate-img-backup.XXXXXXXX)
cleanup() {
    case "$staging" in
        /dev/shm/astrogate-img-backup.*) rm -rf -- "$staging" ;;
    esac
}
trap cleanup EXIT
chmod 700 "$staging"

for name in app upstream data upstream-data proxy-runtime systemd; do
    mkdir -p "$staging/$name"
done
mkdir -p "$staging/host-config/nginx" "$staging/host-config/postfix" \
    "$staging/host-config/docker" "$staging/host-config/mail-relay" \
    "$staging/host-config/ip-access" "$staging/host-config/journald"

rsync -a "$root/app/" "$staging/app/"
rsync -a "$root/upstream/" "$staging/upstream/"
rsync -a --exclude='*.db' --exclude='*.db-*' \
    --exclude='*.sqlite3' --exclude='*.sqlite3-*' \
    "$root/data/" "$staging/data/"
rsync -a "$root/upstream-data/" "$staging/upstream-data/"
if [ -d "$root/proxy-runtime" ]; then
    rsync -a --exclude='cache.db' --exclude='config.full-before-us-only.yaml' \
        "$root/proxy-runtime/" "$staging/proxy-runtime/"
fi
for unit in /etc/systemd/system/astrogate-img-*.service \
            /etc/systemd/system/astrogate-img-*.timer; do
    [ -f "$unit" ] || continue
    cp -p "$unit" "$staging/systemd/"
done
if [ -f /etc/nginx/sites-available/img.astrogate.sbs ]; then
    cp -p /etc/nginx/sites-available/img.astrogate.sbs "$staging/host-config/nginx/"
fi
if [ -f /etc/nginx/sites-available/astrogate-img-ip ]; then
    cp -p /etc/nginx/sites-available/astrogate-img-ip "$staging/host-config/nginx/"
fi
if [ -f /etc/postfix/main.cf ]; then
    cp -p /etc/postfix/main.cf "$staging/host-config/postfix/"
fi
if [ -f /etc/systemd/journald.conf.d/astrogate-img.conf ]; then
    cp -p /etc/systemd/journald.conf.d/astrogate-img.conf \
        "$staging/host-config/journald/"
fi
if [ -f /etc/systemd/system/docker.service.d/astrogate-proxy.conf ]; then
    cp -p /etc/systemd/system/docker.service.d/astrogate-proxy.conf \
        "$staging/host-config/docker/"
fi
if [ -f /etc/astrogate-img/mail-relay-key ]; then
    cp -p /etc/astrogate-img/mail-relay-key \
        /etc/astrogate-img/mail-relay-key.pub "$staging/host-config/mail-relay/"
fi
if [ -f /etc/astrogate-img/ip-access.key ] && [ -f /etc/astrogate-img/ip-access.crt ]; then
    cp -p /etc/astrogate-img/ip-access.key /etc/astrogate-img/ip-access.crt \
        "$staging/host-config/ip-access/"
fi
cp -p "$root/.env" "$root/.upstream.env" "$root/compose.yml" \
    "$root/compose.upstream.yml" "$root/Dockerfile" \
    "$root/requirements.txt" "$staging/"

python3 - "$root/data" "$staging/data" <<'PY'
import sqlite3
import sys
from pathlib import Path

source_dir, target_dir = (Path(value) for value in sys.argv[1:])
for source in source_dir.iterdir():
    if source.suffix not in {'.db', '.sqlite3'}:
        continue
    target = target_dir / source.name
    with sqlite3.connect(source) as origin, sqlite3.connect(target) as snapshot:
        origin.backup(snapshot)
        assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY

python3 - "$staging/upstream-data/accounts.json" <<'PY'
import json
import sys
from pathlib import Path

json.loads(Path(sys.argv[1]).read_text())
PY

tar -C "$staging" -cf - . | gzip -1
