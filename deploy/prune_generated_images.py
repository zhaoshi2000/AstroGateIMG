"""Expire private IMG history and files, retaining the entire previous calendar month."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3


ROOT = Path('/opt/astrogate-img')
BEIJING = timezone(timedelta(hours=8))
IMAGE_NAME = re.compile(r'[0-9a-f]{24}\.(?:png|jpg|webp)')
ASSET_NAME = re.compile(r'[0-9a-f]{48}\.(?:png|jpg|webp)')


def month_cutoff(now):
    month = now.astimezone(BEIJING).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (month - timedelta(days=1)).replace(day=1)


def prune(root, cutoff, apply=False):
    data = root / 'data'
    path = data / 'astrogate.db'
    mode = 'rw' if apply else 'ro'
    cutoff_stamp = cutoff.timestamp()
    predicate = "status='succeeded' AND julianday(created_at) < julianday(?)"
    args = (cutoff.isoformat(),)
    with closing(sqlite3.connect(f'{path.as_uri()}?mode={mode}', uri=True, timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        expired = {row[0] for row in db.execute('SELECT id FROM image_history WHERE ' + predicate, args)}
        known = {row[0] for row in db.execute('SELECT id FROM image_history')}
        files = []
        for folder, pattern in [('images', IMAGE_NAME), ('gateway-assets', ASSET_NAME)]:
            directory = data / folder
            if directory.is_symlink():
                raise ValueError('Unexpected symbolic link in image storage')
            if not directory.exists():
                continue
            for file in directory.iterdir():
                if not pattern.fullmatch(file.name) or file.is_symlink() or not file.is_file():
                    continue
                try:
                    modified = file.stat().st_mtime
                except FileNotFoundError:
                    continue
                if file.stem in expired or (file.stem not in known and modified < cutoff_stamp):
                    files.append(file)
        if apply:
            db.execute('DELETE FROM image_history WHERE ' + predicate, args)
        db.commit()
    if apply:
        for file in files:
            file.unlink(missing_ok=True)
        marker = root / 'generated-images-retention-cutoff'
        temporary = marker.with_suffix('.next')
        temporary.write_text(str(int(cutoff_stamp)))
        temporary.replace(marker)
    return {'apply': apply, 'before_beijing': cutoff.isoformat(), 'history_rows': len(expired),
            'files': len(files)}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--apply', action='store_true', help='delete expired records and files')
    cli.add_argument('--last-cutoff', action='store_true', help='reapply the last cleanup after restoring a snapshot')
    opts = cli.parse_args()
    cutoff = month_cutoff(datetime.now(BEIJING))
    if opts.last_cutoff:
        marker = ROOT / 'generated-images-retention-cutoff'
        if not marker.exists():
            raise SystemExit(0)
        cutoff = datetime.fromtimestamp(int(marker.read_text()), BEIJING)
    print(json.dumps(prune(ROOT, cutoff, opts.apply)))
