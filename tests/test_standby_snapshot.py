import sqlite3
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from deploy.create_standby_snapshot import create_snapshot


class StandbySnapshotTests(unittest.TestCase):
    def test_database_snapshot_is_consistent_and_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'upstream-data').mkdir()
            (root / 'upstream-data' / 'accounts.json').write_text('[{"name":"before"}]')
            live = root / 'data' / 'astrogate.db'
            with closing(sqlite3.connect(live)) as database:
                database.execute('CREATE TABLE sample (value TEXT)')
                database.execute("INSERT INTO sample VALUES ('before')")
                database.commit()
            result = create_snapshot(root)
            with closing(sqlite3.connect(live)) as database:
                database.execute("UPDATE sample SET value = 'after'")
                database.commit()
            with closing(sqlite3.connect(root / 'replica-snapshot' / 'astrogate.db')) as snapshot:
                self.assertEqual(snapshot.execute('SELECT value FROM sample').fetchone()[0], 'before')
            self.assertIn('astrogate.db', result['databases'])
            self.assertEqual(json.loads((root / 'replica-snapshot' / 'accounts.json').read_text()),
                             [{'name': 'before'}])
            self.assertIn('accounts.json', result['sha256'])


if __name__ == '__main__':
    unittest.main()
