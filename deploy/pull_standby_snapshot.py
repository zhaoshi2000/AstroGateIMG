"""Fetch and verify private primary snapshots without changing live standby data."""

import hashlib
import json
from pathlib import Path
import subprocess
import time


ROOT = Path('/opt/astrogate-img')
SOURCE = 'root@154.94.226.49:./replica-snapshot/'
KEY = '/root/.ssh/astrogate_img_replica_read'


def pull_snapshot(root: Path = ROOT) -> dict[str, object]:
    destination = root / 'replica-snapshot'
    destination.mkdir(mode=0o700, exist_ok=True)
    for attempt in range(3):
        result = subprocess.run(
            ['rsync', '-a', '--numeric-ids', '--partial-dir=.rsync-partial',
             '--exclude=*.next', '-e',
             f'ssh -i {KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=12',
             SOURCE, str(destination) + '/'],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode:
            raise RuntimeError(f'rsync exit {result.returncode}: {result.stderr[-300:]}')
        manifest = json.loads((destination / 'manifest.json').read_text())
        hashes = manifest.get('sha256') or {}
        if hashes and all(
            (destination / name).is_file()
            and hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
            for name, digest in hashes.items()
        ):
            return manifest
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError('Standby snapshot did not match its manifest')


if __name__ == '__main__':
    manifest = pull_snapshot()
    print('Verified standby snapshot from', manifest['created_at'])
