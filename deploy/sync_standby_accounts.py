"""Apply a verified primary projection to the live standby via its local bridge."""

import hashlib
import http.client
import json
import os
from pathlib import Path


SNAPSHOT = Path('/opt/astrogate-img/replica-snapshot')


def projection(snapshot):
    manifest = json.loads((snapshot / 'manifest.json').read_bytes())
    raw = (snapshot / 'accounts.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']['accounts.json']:
        raise ValueError('Account snapshot checksum mismatch')
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('Expected a complete account list')
    accounts = []
    for row in rows:
        if row.get('astrogate_managed_by') == 'astrogate-gateway':
            account = {'source_id': row['astrogate_source_id'], 'access_token': row['access_token']}
            for field in ('email', 'account_id', 'user_id', 'expires_at'):
                if row.get(field):
                    account[field] = row[field]
            accounts.append(account)
    return {'accounts': accounts, 'complete': True}


def synchronize():
    body = json.dumps(projection(SNAPSHOT)).encode()
    connection = http.client.HTTPConnection('127.0.0.1', 43921, timeout=610)
    try:
        connection.request('POST', '/internal/astrogate/accounts/sync', body, {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + os.environ['ASTROGATE_GATEWAY_BRIDGE_KEY'],
        })
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f'Local account bridge returned HTTP {response.status}')
        result = json.loads(response.read(65536))
        print('Live account sync: added or refreshed={}, unchanged={}, removed={}'.format(
            int(result['synced']), int(result['skipped']), int(result['removed'])))
    finally:
        connection.close()


if __name__ == '__main__':
    synchronize()
