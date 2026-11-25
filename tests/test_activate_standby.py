import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from deploy.activate_standby import save_and_apply, verify_snapshot
from deploy.create_standby_snapshot import create_snapshot


class ActivateStandbyTests(unittest.TestCase):
    def test_verified_snapshot_replaces_live_state_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').mkdir()
            (root / 'upstream-data').mkdir()
            for name in ('astrogate.db', 'gateway-bridge.sqlite3'):
                db = sqlite3.connect(root / 'data' / name)
                db.execute('CREATE TABLE value (text TEXT)')
                db.execute("INSERT INTO value VALUES ('before')")
                db.commit()
                db.close()
            for name in ('accounts.json', 'image_index.json'):
                (root / 'upstream-data' / name).write_text('{"value":"before"}')
            create_snapshot(root)
            for name in ('astrogate.db', 'gateway-bridge.sqlite3'):
                db = sqlite3.connect(root / 'data' / name)
                db.execute("UPDATE value SET text='after'")
                db.commit()
                db.close()
            for name in ('accounts.json', 'image_index.json'):
                (root / 'upstream-data' / name).write_text('{"value":"after"}')
            manifest = verify_snapshot(root)
            with patch('deploy.activate_standby.os.chown', create=True):
                backup = save_and_apply(root, manifest)
            db = sqlite3.connect(root / 'data' / 'astrogate.db')
            self.assertEqual(db.execute('SELECT text FROM value').fetchone()[0], 'before')
            db.close()
            db = sqlite3.connect(backup / 'data' / 'astrogate.db')
            self.assertEqual(db.execute('SELECT text FROM value').fetchone()[0], 'after')
            db.close()
            self.assertEqual(json.loads((root / 'upstream-data' / 'accounts.json').read_text())['value'],
                             'before')


if __name__ == '__main__':
    unittest.main()
