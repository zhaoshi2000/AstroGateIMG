"""Activate the latest verified primary snapshot on the standby server.

Run this only during a deliberate failover. The previous local state is saved
under backups/ before it is replaced.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request


ROOT = Path('/opt/astrogate-img')
CLIENT_FILES = ('astrogate.db', 'gateway-bridge.sqlite3')
UPSTREAM_FILES = ('accounts.json', 'image_index.json')


def verify_snapshot(root: Path = ROOT) -> dict:
    snapshot = root / 'replica-snapshot'
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    hashes = manifest.get('sha256') or {}
    required = set(CLIENT_FILES + UPSTREAM_FILES)
    if required - hashes.keys():
        raise RuntimeError('Snapshot is missing required files')
    for name in required:
        path = snapshot / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
            raise RuntimeError(f'Snapshot integrity check failed for {name}')
    return manifest


def save_and_apply(root: Path, manifest: dict) -> Path:
    backup = root / 'backups' / ('standby-before-activation-' +
                                  datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True, mode=0o700)
    for folder, names in (('data', CLIENT_FILES), ('upstream-data', UPSTREAM_FILES)):
        destination = root / folder
        (backup / folder).mkdir(mode=0o700)
        for name in names:
            for suffix in ('', '-wal', '-shm') if name.endswith(('.db', '.sqlite3')) else ('',):
                old = destination / (name + suffix)
                if old.is_file():
                    shutil.copy2(old, backup / folder / old.name)
            source = root / 'replica-snapshot' / name
            temporary = destination / (name + '.activate-next')
            shutil.copy2(source, temporary)
            os.chmod(temporary, 0o600)
            if folder == 'data':
                os.chown(temporary, 10001, 10001)
            temporary.replace(destination / name)
            if folder == 'data':
                for suffix in ('-wal', '-shm'):
                    (destination / (name + suffix)).unlink(missing_ok=True)
    marker = root / 'standby-activated.json'
    marker.write_text(json.dumps({'activated_at': datetime.now(timezone.utc).isoformat(),
                                  'snapshot_at': manifest['created_at'],
                                  'previous_state': str(backup)}))
    os.chmod(marker, 0o600)
    return backup


def primary_healthy() -> bool:
    request = urllib.request.Request('https://img.astrogate.sbs/health')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=6) as response:
            return response.status == 200
    except Exception:
        return False


def activate(root: Path = ROOT, force: bool = False) -> Path:
    if os.geteuid() != 0:
        raise RuntimeError('Run as root')
    if primary_healthy() and not force:
        raise RuntimeError('Primary is healthy; pass --force for a planned switchover')
    subprocess.run(['systemctl', 'stop', 'astrogate-standby-pull.timer',
                    'astrogate-standby-pull.service'], check=True)
    manifest = verify_snapshot(root)
    compose = ['docker', 'compose', '-f', str(root / 'compose.yml'),
               '-f', str(root / 'compose.upstream.yml')]
    subprocess.run([*compose, 'stop', 'client', 'upstream'], cwd=root, check=True)
    backup = save_and_apply(root, manifest)
    subprocess.run([*compose, 'up', '-d', '--no-build'], cwd=root, check=True)
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify the local snapshot without activating')
    parser.add_argument('--force', action='store_true', help='allow a planned switchover while the primary is healthy')
    args = parser.parse_args()
    if args.check:
        print('Snapshot verified from', verify_snapshot()['created_at'])
    else:
        print('Standby activated; previous state at', activate(force=args.force))
