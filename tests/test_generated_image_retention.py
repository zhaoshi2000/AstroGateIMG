from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from deploy import pull_standby_media
from deploy.prune_generated_images import BEIJING, month_cutoff, prune


class ImageRetentionTests(unittest.TestCase):
    def test_month_boundary_history_orphans_and_published_copies(self):
        now = datetime(2026, 11, 30, 19, 15, tzinfo=timezone.utc)
        cutoff = month_cutoff(now)
        self.assertEqual(cutoff, datetime(2026, 11, 1, tzinfo=BEIJING))
        self.assertEqual(month_cutoff(datetime(2027, 1, 1, tzinfo=BEIJING)).month, 12)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            images = data / 'images'
            images.mkdir(parents=True)
            old, boundary, orphan = [s * 24 for s in 'abc']
            db_path = data / 'astrogate.db'
            with closing(sqlite3.connect(db_path)) as db:
                db.execute('CREATE TABLE image_history(id TEXT, created_at TEXT, status TEXT)')
                db.executemany("INSERT INTO image_history VALUES (?, ?, 'succeeded')", [
                    (old, '2026-10-31T15:59:59Z'), (boundary, '2026-10-31T16:00:00Z')])
                db.execute('CREATE TABLE generation_jobs(id TEXT)')
                db.execute('INSERT INTO generation_jobs VALUES (?)', (old,))
                db.commit()
            for name in (old, boundary, orphan):
                path = images / (name + '.png')
                path.write_bytes(b'image')
                os.utime(path, (1, 1))
            (data / 'community').mkdir()
            published = data / 'community' / (old + '.png')
            published.write_bytes(b'published')
            self.assertEqual(prune(root, cutoff)['files'], 2)
            self.assertEqual(len(list(images.iterdir())), 3)
            self.assertEqual(prune(root, cutoff, True)['history_rows'], 1)
            self.assertEqual([p.stem for p in images.iterdir()], [boundary])
            self.assertTrue(published.exists())
            with closing(sqlite3.connect(db_path)) as db:
                self.assertEqual(db.execute('SELECT id FROM image_history').fetchall(), [(boundary,)])
                self.assertEqual(db.execute('SELECT id FROM generation_jobs').fetchall(), [(old,)])
            self.assertEqual(prune(root, cutoff, True)['files'], 0)
            listing = ('-rw-r--r-- 100 2026/10/31 15:59:59 old.png\n'
                       '-rw-r--r-- 110 2026/10/31 16:00:00 new.png\n')
            with patch.object(pull_standby_media, 'ROOT', root), patch.object(pull_standby_media, '_rsync', return_value=listing):
                self.assertEqual(pull_standby_media._remote_files('data/images'), [(110, 'new.png')])
