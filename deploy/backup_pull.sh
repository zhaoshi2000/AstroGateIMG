#!/usr/bin/env bash
set -euo pipefail

# Run on the backup host. Set these variables in a private wrapper or cron.
target=${ASTROGATE_BACKUP_SSH_TARGET:?Set ASTROGATE_BACKUP_SSH_TARGET}
key=${ASTROGATE_BACKUP_SSH_KEY:?Set ASTROGATE_BACKUP_SSH_KEY}
dest=${ASTROGATE_BACKUP_DIR:?Set ASTROGATE_BACKUP_DIR}
prefix=${ASTROGATE_BACKUP_PREFIX:-astrogate-img}
case "$prefix" in
    astrogate-img|astrogate-img-154-daily) ;;
    *) echo 'Invalid image backup archive prefix' >&2; exit 1 ;;
esac

umask 077
mkdir -p "$dest"
stamp=$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)
archive="$dest/$prefix-$stamp.tar.gz"
partial="$archive.partial"
if [ -e "$archive" ] || [ -e "$partial" ]; then
    echo 'Image backup archive already exists' >&2
    exit 1
fi
verify_dir=$(mktemp -d /dev/shm/astrogate-img-verify.XXXXXXXX)
cleanup() {
    rm -f -- "$partial"
    case "$verify_dir" in
        /dev/shm/astrogate-img-verify.*) rm -rf -- "$verify_dir" ;;
    esac
}
trap cleanup EXIT

ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -i "$key" "$target" > "$partial"
gzip -t "$partial"
tar -tzf "$partial" >/dev/null
tar -xOzf "$partial" ./data/astrogate.db > "$verify_dir/astrogate.db"
python3 - "$verify_dir/astrogate.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True) as database:
    assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY
tar -xOzf "$partial" ./upstream-data/accounts.json | python3 -c 'import json,sys; json.load(sys.stdin)'
digest=$(sha256sum "$partial" | cut -d ' ' -f1)
mv "$partial" "$archive"
printf '%s  %s\n' "$digest" "$(basename "$archive")" > "$archive.sha256"
cd "$dest"
sha256sum -c "$(basename "$archive.sha256")"
