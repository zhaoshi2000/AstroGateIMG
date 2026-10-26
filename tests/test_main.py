import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.main import GALLERY_BY_ID, Settings, create_app, database_connection, export_dimensions

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlYb4wAAAAASUVORK5CYII='
REFERENCE = 'data:image/png;base64,' + PNG
PASSWORD = 'secure-password-42'


class AstroGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.mailbox = {}

    def app(self, credits=0, handler=None):
        def upstream(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={'data': [{'b64_json': PNG}]})
        return create_app(Settings(Path(self.directory.name), 'https://upstream.example', 'secret', False,
                                   httpx.MockTransport(handler or upstream), 'operator', PASSWORD, credits,
                                   mail_sender=lambda email, code: self.mailbox.update({email: code})))

    def register(self, client, name='artist'):
        email = name if '@' in name else name + '@example.com'
        sent = client.post('/api/auth/email-code', json={'email': email})
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertNotIn('code', sent.json())
        response = client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'code': self.mailbox[email]})
        self.assertEqual(response.status_code, 201)
        return response.json()['user']

    def login(self, client, name='artist'):
        identifier = name if name == 'operator' or '@' in name else name + '@example.com'
        response = client.post('/api/auth/login', json={'identifier': identifier, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_auth_registration_starts_with_zero_credits(self):
        with TestClient(self.app()) as client:
            user = self.register(client)
            self.assertEqual(user['credits'], 0)
            self.assertFalse(user['is_admin'])
            self.assertEqual(client.get('/api/auth/me').status_code, 200)
            self.assertEqual(client.get('/api/admin/users').status_code, 403)
            self.assertEqual(client.post('/api/images/generations', json={'prompt': 'blue circle'}).status_code, 402)
            self.assertEqual(len(self.calls), 0)
            client.post('/api/auth/logout')
            self.assertEqual(client.get('/api/auth/me').status_code, 401)
            self.login(client)
            self.assertEqual(client.get('/api/auth/me').json()['user']['email'], 'artist@example.com')
            self.assertTrue(client.get('/api/auth/me').json()['user']['email_verified'])

    def test_multi_reference_generation_cost_and_idempotency(self):
        with TestClient(self.app(3)) as client:
            self.register(client)
            payload = {'prompt': 'Use the subject of image 1 and colors of image 2', 'image_data_urls': [REFERENCE, REFERENCE]}
            response = client.post('/api/images/edits', json=payload, headers={'Idempotency-Key': 'same-request'})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data['credits'], 2)
            self.assertEqual(data['item']['reference_count'], 2)
            self.assertEqual(data['item']['credits_cost'], 1)
            self.assertEqual(len(self.calls[0]['images']), 2)
            self.assertEqual(client.get(data['item']['image_src']).content, base64.b64decode(PNG))
            again = client.post('/api/images/edits', json=payload, headers={'Idempotency-Key': 'same-request'})
            self.assertEqual(again.json()['credits'], 2)
            self.assertEqual(len(self.calls), 1)
            history = client.get('/api/images/history').json()['items']
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['mode'], 'image-to-image')
            self.login(client, 'operator')
            review = client.get('/api/admin/jobs').json()['items'][0]
            self.assertEqual(review['prompt'], payload['prompt'])
            self.assertEqual(review['image_url'], '/api/admin/images/files/' + data['item']['id'])
            self.assertEqual(client.get(review['image_url']).content, base64.b64decode(PNG))
            self.assertEqual(client.get('/api/admin/jobs?status=failed').json()['items'], [])
        with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
            db.execute("UPDATE generation_jobs SET prompt = NULL WHERE id = ?", (data['item']['id'],))
        with TestClient(self.app()) as client:
            self.login(client, 'operator')
            self.assertEqual(client.get('/api/admin/jobs').json()['items'][0]['prompt'], payload['prompt'])

    def test_widescreen_and_phone_ratios_reach_both_image_endpoints(self):
        with TestClient(self.app(2)) as client:
            self.register(client)
            landscape = client.post('/api/images/generations', json={'prompt': 'a wide blue horizon'})
            self.assertEqual(landscape.status_code, 200, landscape.text)
            self.assertEqual(landscape.json()['item']['size'], '1536x864')
            portrait = client.post('/api/images/edits', json={'prompt': 'a phone wallpaper', 'size': '864x1536', 'image_data_urls': [REFERENCE]})
            self.assertEqual(portrait.status_code, 200, portrait.text)
            self.assertEqual([call['size'] for call in self.calls], ['1536x864', '864x1536'])
            self.assertEqual(portrait.json()['item']['size'], '864x1536')

    def test_resolution_tiers_export_exact_pixels_and_keep_model_pricing(self):
        source = BytesIO()
        Image.new('RGB', (168, 94), '#305080').save(source, format='PNG')
        encoded = base64.b64encode(source.getvalue()).decode()

        def upstream(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={'data': [{'b64_json': encoded}]})

        expected = {'1K': (1024, 576), '2K': (2048, 1152), '3K': (3072, 1728), '4K': (3840, 2160)}
        with TestClient(self.app(5, upstream)) as client:
            self.register(client)
            for tier, dimensions in expected.items():
                response = client.post('/api/images/generations', json={'prompt': 'blue landscape', 'resolution': tier})
                self.assertEqual(response.status_code, 200, response.text)
                item = response.json()['item']
                self.assertEqual(item['resolution'], tier)
                self.assertEqual(item['output_size'], f'{dimensions[0]}x{dimensions[1]}')
                self.assertEqual(item['source_size'], '168x94')
                self.assertEqual(item['credits_cost'], 1)
                self.assertEqual(self.calls[-1]['size'], item['output_size'])
                with Image.open(BytesIO(client.get(item['image_src']).content)) as result:
                    self.assertEqual(result.size, dimensions)
            portrait = client.post('/api/images/edits', json={'prompt': 'blue portrait', 'size': '864x1536', 'resolution': '2K', 'image_data_urls': [REFERENCE]})
            self.assertEqual(portrait.status_code, 200, portrait.text)
            self.assertEqual(portrait.json()['item']['output_size'], '1152x2048')
            self.assertEqual(self.calls[-1]['size'], '1152x2048')
            self.assertEqual(client.get('/api/wallet').json()['credits'], 0)
            self.assertEqual(client.get('/api/images/history').json()['items'][0]['resolution'], '2K')
            self.assertEqual(client.get('/api/images/history').json()['items'][0]['source_size'], '168x94')
        self.assertEqual(export_dimensions('1024x1024', '4K'), (3840, 3840))
        self.assertEqual(export_dimensions('1536x864', 'original'), None)

    def test_failed_generation_refunds_credits(self):
        def fail(request):
            return httpx.Response(429, json={'error': {'message': 'no available image quota'}})
        with TestClient(self.app(1, fail)) as client:
            self.register(client)
            response = client.post('/api/images/generations', json={'prompt': 'blue circle'})
            self.assertEqual(response.status_code, 429)
            wallet = client.get('/api/wallet').json()
            self.assertEqual(wallet['credits'], 1)
            self.assertEqual([item['amount'] for item in wallet['ledger'][:2]], [1, -1])
            self.assertEqual(client.get('/api/images/history').json()['items'], [])
            self.login(client, 'operator')
            review = client.get('/api/admin/jobs?status=failed').json()['items'][0]
            self.assertEqual(review['prompt'], 'blue circle')
            self.assertIsNone(review['image_url'])

    def test_invalid_reference_does_not_charge(self):
        with TestClient(self.app(2)) as client:
            self.register(client)
            for image in ['data:image/png;base64,invalid*', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,aGVsbG8=']:
                response = client.post('/api/images/edits', json={'prompt': 'edit it', 'image_data_urls': [image]})
                self.assertEqual(response.status_code, 400)
            self.assertEqual(client.get('/api/wallet').json()['credits'], 2)
            self.assertEqual(len(self.calls), 0)

    def test_codes_can_only_be_redeemed_once_and_admin_can_adjust(self):
        with TestClient(self.app()) as client:
            user = self.register(client)
            self.login(client, 'operator')
            codes = client.post('/api/admin/codes', json={'credits': 100, 'count': 2}).json()['codes']
            self.assertEqual(len(codes), 2)
            adjustment = client.post(f"/api/admin/users/{user['id']}/credits", json={'amount': 5, 'reason': 'test top-up'})
            self.assertEqual(adjustment.json()['credits'], 5)
            self.login(client)
            self.assertEqual(client.post('/api/wallet/redeem', json={'code': codes[0].lower()}).json()['credits'], 105)
            self.assertEqual(client.post('/api/wallet/redeem', json={'code': codes[0]}).status_code, 400)
            self.assertEqual(client.get('/api/wallet').json()['credits'], 105)

    def test_other_users_cannot_view_or_delete_images(self):
        with TestClient(self.app(1)) as client:
            self.register(client)
            item = client.post('/api/images/generations', json={'prompt': 'blue circle'}).json()['item']
            client.post('/api/auth/logout')
            self.register(client, 'other_artist')
            self.assertEqual(client.get(item['image_src']).status_code, 404)
            self.assertEqual(client.get('/api/admin/jobs').status_code, 403)
            self.assertEqual(client.get('/api/admin/images/files/' + item['id']).status_code, 403)
            self.assertEqual(client.delete('/api/images/history/' + item['id']).status_code, 404)
            self.assertEqual(client.get('/api/images/history').json()['items'], [])

    def test_concurrent_requests_cannot_overdraw_balance(self):
        async def slow(request):
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={'data': [{'b64_json': PNG}]})
        with TestClient(self.app(1, slow)) as client:
            self.register(client)
            with ThreadPoolExecutor(2) as pool:
                statuses = list(pool.map(lambda _: client.post('/api/images/generations', json={'prompt': 'a circle'}).status_code, range(2)))
            self.assertEqual(sorted(statuses), [200, 402])
            self.assertEqual(client.get('/api/wallet').json()['credits'], 0)
            self.assertEqual(len(client.get('/api/images/history').json()['items']), 1)

    def test_restart_refunds_interrupted_reservation_once(self):
        with TestClient(self.app()) as client:
            user = self.register(client)
        with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
            db.execute("INSERT INTO generation_jobs (id, user_id, request_key, fingerprint, status, created_at) VALUES ('interrupted', ?, 'key', 'fingerprint', 'pending', '2026-10-19')", (user['id'],))
        for _ in range(2):
            with TestClient(self.app()) as client:
                self.login(client)
                self.assertEqual(client.get('/api/wallet').json()['credits'], 1)

    def test_homepage_and_studio_exist(self):
        with TestClient(self.app()) as client:
            self.assertIn('0.05', client.get('/').text)
            self.assertIn('reference-image', client.get('/studio').text)
            self.assertEqual(client.get('/assets/mark.svg').status_code, 200)

    def test_public_community_has_fifteen_complete_posts(self):
        with TestClient(self.app()) as client:
            data = client.get('/api/community/posts').json()
            self.assertIsNone(data['user'])
            self.assertEqual(len(data['items']), 15)
            self.assertEqual(len({post['id'] for post in data['items']}), 15)
            for post in data['items']:
                self.assertEqual(post['author'], '站长精选')
                self.assertGreater(len(post['prompt']), 100)
                self.assertEqual(post['comments_count'], len(GALLERY_BY_ID[post['id']]['comments']))
                self.assertGreater(post['example_likes_count'], 0)
                self.assertTrue(post['comment_preview']['is_example'])
                self.assertGreater(post['width'], 0)
                self.assertEqual(client.get(post['thumbnail']).status_code, 200)
            post_id = data['items'][0]['id']
            self.assertEqual(len(client.get('/api/community/posts/' + post_id).json()['comments']), len(GALLERY_BY_ID[post_id]['comments']))
            self.assertEqual(client.get('/api/community/posts/missing-post').status_code, 404)

    def test_community_interactions_require_login_and_persist(self):
        post_id = 'sakura-after-school'
        path = '/api/community/posts/' + post_id
        base_likes = GALLERY_BY_ID[post_id]['example_likes']
        base_comments = len(GALLERY_BY_ID[post_id]['comments'])
        with TestClient(self.app()) as client:
            self.assertEqual(client.post(path + '/like', json={'liked': True}).status_code, 401)
            self.assertEqual(client.post(path + '/comments', json={'content': 'hello'}).status_code, 401)
            self.register(client)
            for _ in range(2):
                self.assertEqual(client.post(path + '/like', json={'liked': True}).json()['likes_count'], base_likes + 1)
            self.assertEqual(client.post(path + '/comments', json={'content': '   '}).status_code, 422)
            content = '<script>alert("literal text only")</script>'
            saved = client.post(path + '/comments', json={'content': content})
            self.assertEqual(saved.status_code, 201)
            self.assertEqual(saved.json()['comments_count'], base_comments + 1)
            self.assertFalse(saved.json()['comment']['is_example'])
            self.assertEqual(saved.json()['comment']['content'], content)
        with TestClient(self.app()) as client:
            self.login(client)
            data = client.get(path).json()
            self.assertTrue(data['item']['liked'])
            self.assertEqual(data['item']['likes_count'], base_likes + 1)
            self.assertEqual(data['comments'][-1]['content'], content)
            self.assertTrue(data['comments'][-1]['can_delete'])
            self.assertEqual(client.post(path + '/like', json={'liked': False}).json()['likes_count'], base_likes)

    def test_community_comment_ownership_and_public_privacy(self):
        path = '/api/community/posts/sakura-after-school'
        with TestClient(self.app()) as client:
            self.register(client)
            comment = client.post(path + '/comments', json={'content': '喜欢樱花和海湾的层次。'}).json()['comment']
            client.post('/api/auth/logout')
            public = client.get(path)
            self.assertNotIn('artist@example.com', public.text)
            self.assertFalse(public.json()['comments'][-1]['can_delete'])
            self.register(client, 'another_artist')
            self.assertEqual(client.delete('/api/community/comments/' + str(comment['id'])).status_code, 403)
            self.login(client, 'operator')
            self.assertEqual(client.delete('/api/community/comments/' + str(comment['id'])).status_code, 200)
            self.assertEqual(client.get(path).json()['item']['comments_count'], len(GALLERY_BY_ID['sakura-after-school']['comments']))

    def test_registration_requires_verification_and_limits_wrong_codes(self):
        with TestClient(self.app()) as client:
            email = 'verified@example.com'
            self.assertEqual(client.post('/api/auth/register', json={'username': 'bypass', 'password': PASSWORD}).status_code, 422)
            sent = client.post('/api/auth/email-code', json={'email': email})
            self.assertEqual(sent.status_code, 200)
            self.assertEqual(client.post('/api/auth/email-code', json={'email': email}).status_code, 429)
            correct = self.mailbox[email]
            wrong = '000000' if correct != '000000' else '111111'
            for _ in range(5):
                self.assertEqual(client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'code': wrong}).status_code, 400)
            blocked = client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'code': correct})
            self.assertEqual(blocked.status_code, 400)
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                row = db.execute('SELECT * FROM email_verifications WHERE email = ?', (email,)).fetchone()
                self.assertEqual(row['attempts'], 5)
                self.assertNotEqual(row['code_hash'], correct)

    def test_resending_invalidates_previous_code_and_expired_codes_fail(self):
        email = 'resend@example.com'
        with TestClient(self.app()) as client, patch('app.main.secrets.randbelow', side_effect=[111111, 222222]):
            self.assertEqual(client.post('/api/auth/email-code', json={'email': email}).status_code, 200)
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                db.execute("UPDATE email_verifications SET requested_at = '2020-01-01T00:00:00+00:00' WHERE email = ?", (email,))
            self.assertEqual(client.post('/api/auth/email-code', json={'email': email}).status_code, 200)
            self.assertEqual(client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'code': '111111'}).status_code, 400)
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                db.execute("UPDATE email_verifications SET expires_at = '2020-01-01T00:00:00+00:00' WHERE email = ?", (email,))
            self.assertEqual(client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'code': '222222'}).status_code, 400)

    def test_email_cooldown_returns_actual_remaining_seconds(self):
        email = 'countdown@example.com'
        with TestClient(self.app()) as client:
            self.assertEqual(client.post('/api/auth/email-code', json={'email': email}).status_code, 200)
            requested_at = (datetime.now(timezone.utc) - timedelta(seconds=23)).isoformat()
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                db.execute('UPDATE email_verifications SET requested_at = ? WHERE email = ?', (requested_at, email))
            response = client.post('/api/auth/email-code', json={'email': email})
            self.assertEqual(response.status_code, 429)
            self.assertIn(int(response.headers['Retry-After']), (36, 37))
            self.assertNotIn('已发送', response.json()['detail'])

    def test_email_is_case_insensitive_and_duplicate_registration_is_blocked(self):
        with TestClient(self.app()) as client:
            self.register(client)
            self.assertEqual(client.post('/api/auth/email-code', json={'email': 'ARTIST@example.com'}).status_code, 409)
            client.post('/api/auth/logout')
            self.login(client, 'ARTIST@EXAMPLE.COM')
            self.assertEqual(client.get('/api/auth/me').json()['user']['email'], 'artist@example.com')

    def test_existing_account_and_balance_are_preserved(self):
        from app.main import hash_password, now_iso
        with TestClient(self.app()) as client:
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                db.execute('INSERT INTO users (username, password_hash, created_at, credits) VALUES (?, ?, ?, ?)', ('existing_user', hash_password(PASSWORD), now_iso(), 17))
            response = client.post('/api/auth/login', json={'username': 'existing_user', 'password': PASSWORD})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['user']['credits'], 17)
            self.assertIsNone(response.json()['user']['email'])

    def test_smtp_error_is_reported_without_exposing_server_details(self):
        def broken_sender(email, code):
            raise RuntimeError('private-smtp-password-detail')
        settings = Settings(Path(self.directory.name), '', '', False, mail_sender=broken_sender)
        with TestClient(create_app(settings)) as client:
            response = client.post('/api/auth/email-code', json={'email': 'mailfailure@example.com'})
            self.assertEqual(response.status_code, 502)
            self.assertTrue(0 < int(response.headers['Retry-After']) <= 60)
            self.assertNotIn('private-smtp-password-detail', response.text)
            self.assertEqual(client.post('/api/auth/email-code', json={'email': 'mailfailure@example.com'}).status_code, 429)

    def test_smtp_message_contains_plain_and_html_verification_code(self):
        from app.mail import send_registration_code
        settings = Settings(Path(self.directory.name), '', '', False, smtp_host='127.0.0.1')
        with patch('app.mail.smtplib.SMTP') as transport:
            smtp = transport.return_value.__enter__.return_value
            send_registration_code(settings, 'recipient@example.com', '012345')
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'recipient@example.com')
            self.assertIn('noreply@astrogate.sbs', message['From'])
            self.assertIn('012345', message.get_body(preferencelist=('plain',)).get_content())
            self.assertIn('012345', message.get_body(preferencelist=('html',)).get_content())


if __name__ == '__main__':
    unittest.main()
