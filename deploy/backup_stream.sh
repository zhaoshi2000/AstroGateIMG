#!/usr/bin/env bash
set -euo pipefail

# Streams a consistent private backup to stdout. Intended for a restricted
# SSH key; keep stdout exclusively for the tar stream.
root=/opt/astrogate-img
staging=$(mktemp -d /tmp/astrogate-img-backup.XXXXXXXX)
trap 'rm -rf "$staging"' EXIT
chmod 700 "$staging"

for name in app upstream data upstream-data; do
    mkdir -p "$staging/$name"
done

rsync -a "$root/app/" "$staging/app/"
rsync -a "$root/upstream/" "$staging/upstream/"
rsync -a --exclude='*.db' --exclude='*.db-*' \
    --exclude='*.sqlite3' --exclude='*.sqlite3-*' \
    "$root/data/" "$staging/data/"
rsync -a "$root/upstream-data/" "$staging/upstream-data/"
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
