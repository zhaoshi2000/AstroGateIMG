"""Write consistent SQLite snapshots for the standby image server."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


ROOT = Path('/opt/astrogate-img')
DATABASES = ('astrogate.db', 'gateway-bridge.sqlite3')
UPSTREAM_FILES = ('accounts.json', 'image_index.json')


def create_snapshot(root: Path = ROOT) -> dict[str, object]:
    source_dir = root / 'data'
    destination_dir = root / 'replica-snapshot'
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    sizes = {}
    hashes = {}
    for name in DATABASES:
        source = source_dir / name
        if not source.is_file():
            continue
        temporary = destination_dir / (name + '.next')
        destination = destination_dir / name
        temporary.unlink(missing_ok=True)
        with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=20)) as origin:
            with closing(sqlite3.connect(temporary)) as target:
                origin.backup(target)
                target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                target.commit()
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
        sizes[name] = destination.stat().st_size
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    upstream_sizes = {}
    for name in UPSTREAM_FILES:
        source = root / 'upstream-data' / name
        if not source.is_file():
            continue
        content = source.read_bytes()
        json.loads(content)
        temporary = destination_dir / (name + '.next')
        temporary.write_bytes(content)
        os.chmod(temporary, 0o600)
        temporary.replace(destination_dir / name)
        upstream_sizes[name] = len(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                'databases': sizes, 'upstream_files': upstream_sizes,
                'sha256': hashes}
    temporary = destination_dir / 'manifest.json.next'
    temporary.write_text(json.dumps(manifest), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(destination_dir / 'manifest.json')
    return manifest


if __name__ == '__main__':
    print(json.dumps(create_snapshot()))
