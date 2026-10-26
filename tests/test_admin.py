import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from fastapi.testclient import TestClient

from app.main import Settings, create_app, database_connection, generation_failure_reason
from app.upstream_admin import project_accounts


def token(issued):
    claims = {'iat': issued, 'https://api.openai.com/auth': {'chatgpt_plan_type': 'team'}}
    return 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.private-signature'


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.mailbox = {}
        self.records = [
            {'access_token': token(1), 'account_id': 'workspace-1', 'email': 'first@example.com', 'status': '正常', 'quota': 120, 'refresh_token': 'private-refresh-token'},
            {'access_token': token(2), 'account_id': 'workspace-1', 'email': 'first@example.com', 'status': '正常', 'quota': 110, 'id_token': 'private-id-token'},
            {'access_token': token(3), 'account_id': 'workspace-2', 'email': 'second@example.com', 'status': '限流', 'quota': 40},
        ]

    def app(self, failing=False):
        def upstream(request):
            self.calls.append(request)
            self.assertEqual(request.headers['authorization'], 'Bearer private-upstream-key')
            if failing:
                return httpx.Response(500, json={'detail': 'private-upstream-key'})
            if request.url.path == '/api/accounts':
                return httpx.Response(200, json={'items': self.records})
            if request.url.path == '/api/accounts/refresh':
                return httpx.Response(200, json={'progress_id': 'test-progress-1'})
            return httpx.Response(200, json={'total': 2, 'processed': 2, 'done': True, 'total_quota': 220, 'result': {'items': self.records, 'errors': [{'error': 'private-refresh-token'}]}})
        return create_app(Settings(Path(self.directory.name), 'https://upstream.example', 'private-upstream-key', False,
                                   httpx.MockTransport(upstream), 'operator', 'admin-password-123', mail_sender=lambda email, code: self.mailbox.update({email: code})))

    def login(self, client):
        response = client.post('/api/admin/login', json={'username': 'operator', 'password': 'admin-password-123'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['set-cookie'])

    def test_admin_document_and_permissions_are_separate(self):
        with TestClient(self.app()) as client:
            html = client.get('/admin/accounts').text
            self.assertIn('/assets/admin.js', html)
            self.assertNotIn('/assets/app.js', html)
            self.assertNotIn('generation-form', html)
            studio = client.get('/studio').text
            self.assertNotIn('code-create-form', studio)
            self.assertNotIn('admin-login-form', studio)
            self.assertEqual(client.get('/api/admin/upstream/accounts').status_code, 401)
            email = 'customer@example.com'
            client.post('/api/auth/email-code', json={'email': email})
            client.post('/api/auth/register', json={'email': email, 'password': 'customer-password', 'code': self.mailbox[email]})
            self.assertEqual(client.get('/api/admin/me').status_code, 403)
            self.assertEqual(client.get('/api/admin/overview').status_code, 403)
            self.assertEqual(client.get('/api/admin/users/1/records').status_code, 403)
            self.assertEqual(client.get('/api/admin/upstream/accounts').status_code, 403)
            self.assertEqual(client.post('/api/admin/upstream/refresh', json={}).status_code, 403)
            self.assertEqual(len(self.calls), 0)

    def test_quota_aggregation_deduplicates_and_never_returns_credentials(self):
        with TestClient(self.app()) as client:
            self.login(client)
            response = client.get('/api/admin/upstream/accounts')
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data['stats']['accounts'], 2)
            self.assertEqual(data['stats']['records'], 3)
            self.assertEqual(data['stats']['image_quota'], 110)
            self.assertEqual(data['items'][0]['credential_records'], 2)
            self.assertEqual(data['items'][0]['plan_type'], 'team')
            for private in ['access_token', 'refresh_token', 'id_token', 'private-upstream-key', 'private-refresh-token', 'private-signature']:
                self.assertNotIn(private, response.text)

    def test_selected_refresh_and_progress_keep_tokens_on_server(self):
        with TestClient(self.app()) as client:
            self.login(client)
            account_id = client.get('/api/admin/upstream/accounts').json()['items'][0]['id']
            response = client.post('/api/admin/upstream/refresh', json={'account_ids': [account_id]})
            self.assertEqual(response.status_code, 200)
            forwarded = json.loads(self.calls[-1].content)
            self.assertEqual(set(forwarded['access_tokens']), {token(1), token(2)})
            progress = client.get('/api/admin/upstream/refresh/test-progress-1')
            self.assertTrue(progress.json()['done'])
            self.assertEqual(progress.json()['error_count'], 1)
            self.assertNotIn('private', progress.text)
            self.assertNotIn('result', progress.json())
            self.assertEqual(client.post('/api/admin/upstream/refresh', json={'account_ids': ['missing']}).status_code, 400)

    def test_local_management_survives_upstream_failure_without_leaking_errors(self):
        with TestClient(self.app(failing=True)) as client:
            self.login(client)
            self.assertEqual(client.get('/api/admin/overview').status_code, 200)
            response = client.get('/api/admin/upstream/accounts')
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('private-upstream-key', response.text)

    def test_recent_jobs_show_safe_failure_cause(self):
        self.assertEqual(generation_failure_reason('生成的图片可能违反内容政策'), '生成结果被上游内容审核拦截')
        self.assertEqual(generation_failure_reason('上游超时'), '上游生成超时')
        with TestClient(self.app()) as client:
            self.login(client)
            db_path = Path(self.directory.name) / 'astrogate.db'
            with database_connection(db_path) as db:
                user_id = db.execute("SELECT id FROM users WHERE username = 'operator'").fetchone()[0]
                db.execute("INSERT INTO generation_jobs (id, user_id, request_key, fingerprint, status, created_at, error_message, credits_cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           ('failed-1', user_id, 'request-1', 'fingerprint', 'failed', '2026-10-26T00:00:00Z',
                            '提示可能违反了我们的内容政策 private-upstream-key', 1))
            response = client.get('/api/admin/overview')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['recent_jobs'][0]['failure_reason'], '提示词被上游内容审核拦截')
            self.assertNotIn('private-upstream-key', response.text)
            records = client.get(f'/api/admin/users/{user_id}/records')
            self.assertEqual(records.status_code, 200)
            self.assertEqual(records.json()['jobs'][0]['failure_reason'], '提示词被上游内容审核拦截')
            self.assertEqual(records.json()['user']['username'], 'operator')
            self.assertNotIn('private-upstream-key', records.text)
            self.assertEqual(client.get('/api/admin/users/999999/records').status_code, 404)

    def test_malformed_claims_are_not_trusted_as_account_objects(self):
        invalid = 'header.' + base64.urlsafe_b64encode(b'[]').decode() + '.signature'
        public, _ = project_accounts([{'access_token': invalid, 'email': 'reader@example.com'}])
        self.assertEqual(public['stats']['accounts'], 1)


if __name__ == '__main__':
    unittest.main()
