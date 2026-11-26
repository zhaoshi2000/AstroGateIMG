import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from deploy.sync_standby_accounts import projection


class StandbyAccountsTest(unittest.TestCase):
    def test_only_verified_gateway_authorizations_are_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            raw = json.dumps([
                {'astrogate_managed_by': 'astrogate-gateway', 'astrogate_source_id': '7',
                 'access_token': 'test-access', 'refresh_token': 'test-refresh', 'quota': 20},
                {'source_type': 'local', 'access_token': 'local-only'},
            ]).encode()
            path = snapshot / 'accounts.json'
            path.write_bytes(raw)
            (snapshot / 'manifest.json').write_text(json.dumps({
                'sha256': {'accounts.json': hashlib.sha256(raw).hexdigest()}}))
            self.assertEqual(projection(snapshot), {'accounts': [
                {'source_id': '7', 'access_token': 'test-access'}], 'complete': True})
            path.write_text('[]')
            with self.assertRaises(ValueError):
                projection(snapshot)
