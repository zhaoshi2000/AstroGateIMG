import base64
from io import BytesIO
import json
from pathlib import Path
import unittest

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.main import GALLERY_POSTS, database_connection
import test_main as fixtures


def image_data(size=(32, 24)):
    output = BytesIO()
    Image.new('RGB', size, '#8c65dc').save(output, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()


class CommunityPublishingTests(unittest.TestCase):
    setUp = fixtures.AstroGateTests.setUp
    app = fixtures.AstroGateTests.app
    register = fixtures.AstroGateTests.register
    login = fixtures.AstroGateTests.login

    def publish(self, client, **changes):
        payload = {'title': '我的创作分享', 'content': '试试不一样的光线。', 'prompt': '柔和紫色光线',
                   'category': 'design', 'tags': ['配色', '灵感'], 'image_data_urls': [image_data()]}
        response = client.post('/api/community/posts', json={**payload, **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['item']

    def test_multi_image_posts_are_public_persistent_and_free(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.post('/api/community/posts', json={'title': 'test', 'content': 'test'}).status_code, 401)
            self.register(client)
            item = self.publish(client, image_data_urls=[image_data(), image_data((24, 32))])
            self.assertEqual(len(item['images']), 2)
            self.assertTrue(item['is_mine'] and item['can_delete'])
            self.assertFalse(item['is_curated'])
            self.assertEqual(item['likes_count'], 0)
            self.assertEqual(item['example_likes_count'], 0)
            self.assertIsNone(item['comment_preview'])
            self.assertEqual(client.get('/api/wallet').json()['credits'], 0)
            for image in item['images']:
                response = client.get(image['image'])
                self.assertEqual(response.headers['content-type'], 'image/webp')
                self.assertEqual(Image.open(BytesIO(response.content)).format, 'WEBP')
            client.post('/api/auth/logout')
            public = client.get('/api/community/posts/' + item['id'])
            self.assertNotIn('artist@example.com', public.text)
            self.assertNotIn('user_id', public.json()['item'])
            self.assertFalse(public.json()['item']['can_delete'])
            self.assertEqual(public.json()['comments'], [])
            self.assertEqual(client.get(item['image']).status_code, 200)
            self.assertEqual(client.get('/api/community/posts?mine=true').status_code, 401)
        with TestClient(self.app()) as client:
            self.login(client)
            mine = client.get('/api/community/posts?mine=true').json()['items']
            self.assertEqual([post['id'] for post in mine], [item['id']])

    def test_post_ownership_comment_likes_and_admin_cleanup(self):
        with TestClient(self.app()) as client:
            self.register(client)
            item = self.publish(client)
            path = '/api/community/posts/' + item['id']
            self.register(client, 'second_artist')
            self.assertEqual(client.get('/api/community/posts?mine=true').json()['items'], [])
            self.assertEqual(client.delete(path).status_code, 403)
            for _ in range(2):
                self.assertEqual(client.post(path + '/like', json={'liked': True}).json()['likes_count'], 1)
            response = client.post(path + '/comments', json={'content': '<script>literal text</script>'})
            self.assertEqual(response.json()['comments_count'], 1)
            self.assertFalse(response.json()['comment']['is_example'])
            self.login(client, 'operator')
            self.assertTrue(client.get(path).json()['item']['can_delete'])
            self.assertEqual(client.delete(path).status_code, 200)
            self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.get(item['image']).status_code, 404)
            self.assertEqual(client.post(path + '/like', json={'liked': True}).status_code, 404)
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM community_likes WHERE post_id = ?', (item['id'],)).fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM community_comments WHERE post_id = ?', (item['id'],)).fetchone()[0], 0)
            self.assertFalse((Path(self.directory.name) / 'community' / item['id']).exists())

    def test_private_history_requires_owner_and_public_copy_survives_private_deletion(self):
        def upstream(_):
            return httpx.Response(200, json={'data': [{'b64_json': image_data().split(',')[1]}]})
        with TestClient(self.app(1, upstream)) as client:
            self.register(client)
            private = client.post('/api/images/generations', json={'prompt': 'private original prompt'}).json()['item']
            self.register(client, 'another_artist')
            response = client.post('/api/community/posts', json={'title': 'forbidden', 'source_history_id': private['id']})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(client.get(private['image_src']).status_code, 404)
            self.login(client)
            item = self.publish(client, source_history_id=private['id'], image_data_urls=[])
            self.assertEqual(client.delete('/api/images/history/' + private['id']).status_code, 200)
            client.post('/api/auth/logout')
            self.assertEqual(client.get(item['image']).status_code, 200)
            self.assertEqual(client.get(private['image_src']).status_code, 401)

    def test_invalid_images_and_payloads_do_not_leave_posts_or_files(self):
        with TestClient(self.app()) as client:
            self.register(client)
            for changes in [
                {'title': '   ', 'content': 'text'},
                {'title': 'empty'},
                {'title': 'invalid', 'image_data_urls': ['data:image/svg+xml;base64,PHN2Zz4=']},
                {'title': 'invalid', 'image_data_urls': ['data:image/png;base64,broken']},
                {'title': 'invalid', 'image_data_urls': ['data:image/png;base64,' + base64.b64encode(b'not an image').decode()]},
                {'title': 'invalid', 'image_data_urls': [image_data()] * 10},
                {'title': 'invalid', 'content': 'body', 'tags': ['x' * 21]},
                {'title': 'invalid', 'source_history_id': '../../private'},
            ]:
                self.assertIn(client.post('/api/community/posts', json=changes).status_code, (400, 422))
            self.assertEqual(client.get('/api/community/posts?mine=true').json()['items'], [])
            self.assertFalse(list((Path(self.directory.name) / 'community').glob('*')))
            self.assertEqual(client.get('/community-media/not-valid/0.webp').status_code, 404)
            self.assertEqual(client.get('/community-media/post-' + 'a' * 24 + '/9.webp').status_code, 404)

    def test_text_posts_rate_limit_and_feed_pagination(self):
        with TestClient(self.app()) as client:
            user = self.register(client)
            for index in range(3):
                item = self.publish(client, title=f'text {index}', image_data_urls=[])
                self.assertEqual(item['images'], [])
            blocked = client.post('/api/community/posts', json={'title': 'too fast', 'content': 'text'})
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers['retry-after'], '60')
            with database_connection(Path(self.directory.name) / 'astrogate.db') as db:
                for index in range(35):
                    db.execute('INSERT INTO community_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               ('post-' + f'{index:024x}', user['id'], 'older post', 'content', '', 'general', '[]', '[]', '1024x1024', '2026-01-01T00:00:00+00:00'))
            first = client.get('/api/community/posts?mine=true').json()
            second = client.get('/api/community/posts?mine=true&page=1').json()
            self.assertTrue(first['has_more'])
            self.assertFalse(second['has_more'])
            self.assertEqual(len(first['items']), 30)
            self.assertEqual(len(second['items']), 8)
            self.assertFalse({p['id'] for p in first['items']} & {p['id'] for p in second['items']})
            self.assertEqual(len(client.get('/api/community/posts').json()['items']), 45)

    def test_example_interactions_vary_and_remain_stable(self):
        with TestClient(self.app()) as client:
            first = client.get('/api/community/posts').json()['items']
            second = client.get('/api/community/posts').json()['items']
            self.assertEqual(first, second)
            self.assertGreater(len({p['likes_count'] for p in first}), 10)
            self.assertGreater(len({p['comments_count'] for p in first}), 3)
            self.assertTrue(all(p['real_likes_count'] == 0 for p in first))
            self.assertEqual(len(first), len(GALLERY_POSTS))


if __name__ == '__main__':
    unittest.main()
