import asyncio
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from threading import Lock
import time

import httpx

from app.main import Settings, create_app, database_connection, prepare_generated_image


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlYb4wAAAAASUVORK5CYII='


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'astrogate.db'
        self.calls = []
        self.release = asyncio.Event()
        self.release.set()
        self.started = asyncio.Event()
        self.failing = False

        async def upstream(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'data': [{'id': 'gpt-image-2'}]})
            self.calls.append(json.loads(request.content))
            self.started.set()
            await self.release.wait()
            if self.failing:
                return httpx.Response(503, json={'detail': 'upstream unavailable'})
            return httpx.Response(200, json={'data': [{'b64_json': PNG}]})

        app = create_app(Settings(Path(self.directory.name), 'https://upstream.example', 'upstream-secret', False,
                                 httpx.MockTransport(upstream), 'operator', 'admin-password-123'))
        self.life = app.router.lifespan_context(app)
        await self.life.__aenter__()
        self.addAsyncCleanup(self.life.__aexit__, None, None, None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')
        self.addAsyncCleanup(self.client.aclose)
        login = await self.client.post('/api/admin/login', json={'username': 'operator', 'password': 'admin-password-123'})
        self.assertEqual(login.status_code, 200)
        with database_connection(self.path) as db:
            self.admin_id = db.execute("SELECT id FROM users WHERE username = 'operator'").fetchone()[0]
            db.execute('UPDATE users SET credits = 100 WHERE id = ?', (self.admin_id,))

    async def key(self, limit=3):
        result = await self.client.post('/api/admin/api-keys', json={'name': 'Python script', 'max_concurrency': limit})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def counts(self):
        with database_connection(self.path) as db:
            return (db.execute('SELECT credits FROM users WHERE id = ?', (self.admin_id,)).fetchone()[0],
                    db.execute('SELECT COUNT(*) FROM generation_jobs').fetchone()[0])

    async def test_keys_are_hashed_scoped_revocable_and_require_current_admin(self):
        created = await self.key()
        secret = created['key']
        headers = {'Authorization': 'Bearer ' + secret}
        listing = await self.client.get('/api/admin/api-keys')
        self.assertNotIn(secret, listing.text)
        with database_connection(self.path) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM admin_api_keys')]
        self.assertNotIn(secret, json.dumps(rows))
        self.assertNotIn('key_hash', listing.text)
        self.assertEqual((await self.client.get('/v1/models', headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get('/v1/models')).status_code, 401)
        self.assertEqual((await self.client.get('/v1/models', headers={'Authorization': 'Bearer wrong'})).status_code, 401)
        cookies = dict(self.client.cookies)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get('/api/admin/users', headers=headers)).status_code, 401)
        self.assertEqual((await self.client.post('/api/admin/api-keys', json={'name': 'bad'}, headers=headers)).status_code, 401)
        self.client.cookies.update(cookies)
        with database_connection(self.path) as db:
            db.execute('UPDATE users SET is_admin = 0 WHERE id = ?', (self.admin_id,))
        self.assertEqual((await self.client.get('/v1/models', headers=headers)).status_code, 401)
        self.assertEqual((await self.client.post('/api/admin/images/generations', json={'prompt': 'blue moon'})).status_code, 403)
        self.assertEqual((await self.client.post('/api/admin/api-keys', json={'name': 'bad'})).status_code, 403)
        with database_connection(self.path) as db:
            db.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (self.admin_id,))
        self.assertEqual((await self.client.delete('/api/admin/api-keys/' + created['item']['id'])).status_code, 200)
        self.assertEqual((await self.client.get('/v1/models', headers=headers)).status_code, 401)

    async def test_generation_edits_billing_idempotency_and_private_files(self):
        created = await self.key()
        headers = {'Authorization': 'Bearer ' + created['key'], 'Idempotency-Key': 'one-image'}
        payload = {'prompt': 'blue moon', 'image_data_urls': ['data:image/png;base64,' + PNG]}
        response = await self.client.post('/v1/images/edits', headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(base64.b64decode(body['data'][0]['b64_json']), base64.b64decode(PNG))
        self.assertEqual(body['credits_cost'], 1)
        self.assertEqual(len(self.calls[0]['images']), 1)
        again = await self.client.post('/v1/images/edits', headers=headers, json={**payload, 'response_format': 'url'})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()['id'], body['id'])
        self.assertEqual(self.counts(), (99, 1))
        self.assertEqual(len(self.calls), 1)
        url = again.json()['data'][0]['url']
        self.assertEqual((await self.client.get(url)).status_code, 401)
        self.assertEqual((await self.client.get(url, headers=headers)).content, base64.b64decode(PNG))
        audit = (await self.client.get('/api/admin/jobs')).json()['items'][0]
        self.assertEqual(audit['prompt'], 'blue moon')
        self.assertEqual(audit['status'], 'succeeded')
        self.assertEqual((await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'different'})).status_code, 409)
        self.assertEqual((await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'moon', 'n': 2})).status_code, 422)

    async def test_parallel_key_limit_rejects_before_charge_and_releases_after_failure(self):
        created = await self.key(2)
        headers = {'Authorization': 'Bearer ' + created['key']}
        self.release.clear()
        pending = [asyncio.create_task(self.client.post('/v1/images/generations', headers=headers, json={'prompt': f'moon {i}'})) for i in range(2)]
        try:
            for _ in range(200):
                if len(self.calls) == 2:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(len(self.calls), 2)
            rejected = await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'overflow'})
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertEqual(rejected.headers['retry-after'], '2')
            self.assertEqual(self.counts(), (98, 2))
            self.failing = True
        finally:
            self.release.set()
            results = await asyncio.gather(*pending)
        self.assertEqual([r.status_code for r in results], [502, 502])
        self.assertEqual(self.counts(), (100, 2))
        self.failing = False
        self.assertEqual((await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'retry'})).status_code, 200)
        self.assertEqual(self.counts(), (99, 3))

    async def test_all_keys_and_panel_share_admin_ceiling(self):
        keys = [await self.key(8), await self.key(8)]
        self.release.clear()
        pending = [asyncio.create_task(self.client.post('/api/admin/images/generations', json={'prompt': f'moon {i}'})) for i in range(6)]
        pending.append(asyncio.create_task(self.client.post('/api/images/generations', json={'prompt': 'studio seventh'})))
        pending.append(asyncio.create_task(self.client.post('/v1/images/generations', headers={'Authorization': 'Bearer ' + keys[0]['key']}, json={'prompt': 'eighth'})))
        try:
            for _ in range(200):
                if len(self.calls) == 8:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(len(self.calls), 8)
            result = await asyncio.wait_for(self.client.post('/v1/images/generations', headers={'Authorization': 'Bearer ' + keys[1]['key']}, json={'prompt': 'overflow'}), 1)
            self.assertEqual(result.status_code, 429, result.text)
            studio_result = await asyncio.wait_for(self.client.post('/api/images/edits', json={'prompt': 'studio overflow', 'image_data_urls': ['data:image/png;base64,' + PNG]}), 1)
            self.assertEqual(studio_result.status_code, 429, studio_result.text)
            self.assertEqual(self.counts(), (92, 8))
        finally:
            self.release.set()
            results = await asyncio.gather(*pending)
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(self.counts(), (92, 8))

    async def test_image_processing_does_not_serialize_concurrent_generations(self):
        lock = Lock()
        active = peak = 0

        def process(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.2)
            try:
                return prepare_generated_image(*args)
            finally:
                with lock:
                    active -= 1

        with patch('app.main.prepare_generated_image', side_effect=process):
            results = await asyncio.gather(*[
                self.client.post('/api/admin/images/generations', json={'prompt': f'parallel {i}'})
                for i in range(2)
            ])
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(peak, 2)

    async def test_cancelled_request_refunds_and_frees_its_slot(self):
        created = await self.key(1)
        headers = {'Authorization': 'Bearer ' + created['key']}
        self.release.clear()
        task = asyncio.create_task(self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'cancel me'}))
        await asyncio.wait_for(self.started.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.counts(), (100, 1))
        self.release.set()
        result = await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'after cancellation'})
        self.assertEqual(result.status_code, 200, result.text)

    async def test_other_admin_key_cannot_access_image_or_revoke_key(self):
        created = await self.key()
        headers = {'Authorization': 'Bearer ' + created['key']}
        result = await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'private moon', 'response_format': 'url'})
        self.assertEqual(result.status_code, 200)
        with database_connection(self.path) as db:
            user = db.execute('SELECT * FROM users WHERE id = ?', (self.admin_id,)).fetchone()
            db.execute('INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, 1)', ('otheradmin', user['password_hash'], user['created_at']))
        login = await self.client.post('/api/admin/login', json={'username': 'otheradmin', 'password': 'admin-password-123'})
        self.assertEqual(login.status_code, 200)
        other = await self.key()
        self.assertEqual((await self.client.delete('/api/admin/api-keys/' + created['item']['id'])).status_code, 404)
        denied = await self.client.get(result.json()['data'][0]['url'], headers={'Authorization': 'Bearer ' + other['key']})
        self.assertEqual(denied.status_code, 404)

    async def test_duplicate_inflight_request_does_not_double_bill(self):
        created = await self.key()
        headers = {'Authorization': 'Bearer ' + created['key'], 'Idempotency-Key': 'parallel-duplicate'}
        self.release.clear()
        task = asyncio.create_task(self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'same moon'}))
        try:
            await asyncio.wait_for(self.started.wait(), 3)
            duplicate = await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'same moon'})
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            self.assertEqual(self.counts(), (99, 1))
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            result = await task
        retry = await self.client.post('/v1/images/generations', headers=headers, json={'prompt': 'same moon'})
        self.assertEqual(retry.json()['id'], result.json()['id'])
        self.assertEqual(self.counts(), (99, 1))


if __name__ == '__main__':
    unittest.main()
