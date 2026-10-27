#!/usr/bin/env bash
set -euo pipefail

# Run on the backup host. Set these variables in a private wrapper or cron.
target=${ASTROGATE_BACKUP_SSH_TARGET:?Set ASTROGATE_BACKUP_SSH_TARGET}
key=${ASTROGATE_BACKUP_SSH_KEY:?Set ASTROGATE_BACKUP_SSH_KEY}
dest=${ASTROGATE_BACKUP_DIR:?Set ASTROGATE_BACKUP_DIR}

umask 077
mkdir -p "$dest"
stamp=$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)
archive="$dest/astrogate-img-$stamp.tar.gz"
partial="$archive.partial"
trap 'rm -f "$partial"' EXIT

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i "$key" "$target" > "$partial"
tar -tzf "$partial" >/dev/null
mv "$partial" "$archive"
sha256sum "$archive" > "$archive.sha256"
cd "$dest"
sha256sum -c "$(basename "$archive.sha256")"
find "$dest" -maxdepth 1 -type f \( -name 'astrogate-img-*.tar.gz' -o -name 'astrogate-img-*.tar.gz.sha256' \) -mtime +30 -delete
