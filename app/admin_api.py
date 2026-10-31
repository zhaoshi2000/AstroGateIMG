"""Administrator image API keys and bounded, shared generation admission."""

import base64
from contextlib import contextmanager
from datetime import datetime
import re
import secrets
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator


MAX_ADMIN_CONCURRENCY = 8
KEY_COLUMNS = 'id, name, prefix, max_concurrency, created_at, last_used_at, revoked_at'


class KeyCreation(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    max_concurrency: int = Field(default=3, ge=1, le=MAX_ADMIN_CONCURRENCY)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError('请填写密钥名称')
        return value.strip()


def initialize_admin_api_database(path, database_connection):
    with database_connection(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS admin_api_keys (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key_hash TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                max_concurrency INTEGER NOT NULL CHECK(max_concurrency BETWEEN 1 AND 8),
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS admin_api_keys_by_user ON admin_api_keys(user_id, created_at DESC);
        ''')


class GenerationAdmission:
    """One process owns generation jobs; reserve no credits until admitted."""

    def __init__(self):
        self._lock = Lock()
        self._users = {}
        self._keys = {}

    @contextmanager
    def slot(self, user_id, key=None):
        key_id = key['id'] if key else None
        with self._lock:
            if self._users.get(user_id, 0) >= MAX_ADMIN_CONCURRENCY or (
                key and self._keys.get(key_id, 0) >= key['max_concurrency']
            ):
                raise HTTPException(429, '并发任务已达上限，请稍后重试', headers={'Retry-After': '2'})
            self._users[user_id] = self._users.get(user_id, 0) + 1
            if key:
                self._keys[key_id] = self._keys.get(key_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._users[user_id] -= 1
                if not self._users[user_id]:
                    del self._users[user_id]
                if key:
                    self._keys[key_id] -= 1
                    if not self._keys[key_id]:
                        del self._keys[key_id]


def create_admin_api_router(*, database_path, image_dir, database_connection, hash_token,
                            now_iso, public_user, admin_user, image_model_items,
                            run_generation, generation_request_type, edit_request_type, admission):
    router = APIRouter()

    class ApiGenerationRequest(generation_request_type):
        n: Literal[1] = 1
        response_format: Literal['b64_json', 'url'] = 'b64_json'

    class ApiEditRequest(edit_request_type):
        n: Literal[1] = 1
        response_format: Literal['b64_json', 'url'] = 'b64_json'

    def api_principal(authorization: Annotated[str | None, Header()] = None):
        scheme, _, token = (authorization or '').partition(' ')
        if scheme.lower() != 'bearer' or not re.fullmatch(r'agimg_[A-Za-z0-9_-]{43}', token):
            raise HTTPException(401, '需要有效的管理员 API 密钥', headers={'WWW-Authenticate': 'Bearer'})
        with database_connection(database_path) as db:
            key = db.execute('SELECT * FROM admin_api_keys WHERE key_hash = ? AND revoked_at IS NULL', (hash_token(token),)).fetchone()
            user = db.execute('SELECT * FROM users WHERE id = ? AND is_admin = 1', (key['user_id'],)).fetchone() if key else None
            if not user:
                raise HTTPException(401, 'API 密钥无效或已撤销', headers={'WWW-Authenticate': 'Bearer'})
            db.execute('UPDATE admin_api_keys SET last_used_at = ? WHERE id = ?', (now_iso(), key['id']))
            return {'user': public_user(user), 'key': dict(key)}

    @router.get('/api/admin/api-keys')
    def list_keys(user=Depends(admin_user)):
        with database_connection(database_path) as db:
            items = db.execute(f'SELECT {KEY_COLUMNS} FROM admin_api_keys WHERE user_id = ? ORDER BY created_at DESC', (user['id'],)).fetchall()
        return {'items': [dict(row) for row in items], 'max_concurrency': MAX_ADMIN_CONCURRENCY}

    @router.post('/api/admin/api-keys', status_code=201)
    def create_key(body: KeyCreation, user=Depends(admin_user)):
        token = 'agimg_' + secrets.token_urlsafe(32)
        item = {'id': secrets.token_hex(12), 'name': body.name, 'prefix': token[:13],
                'max_concurrency': body.max_concurrency, 'created_at': now_iso(),
                'last_used_at': None, 'revoked_at': None}
        with database_connection(database_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM admin_api_keys WHERE user_id = ? AND revoked_at IS NULL', (user['id'],)).fetchone()[0] >= 20:
                raise HTTPException(400, '最多保留 20 个有效密钥，请先撤销不用的密钥')
            db.execute('INSERT INTO admin_api_keys (id, user_id, key_hash, name, prefix, max_concurrency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (item['id'], user['id'], hash_token(token), item['name'], item['prefix'], item['max_concurrency'], item['created_at']))
        return {'key': token, 'item': item}

    @router.delete('/api/admin/api-keys/{key_id}')
    def revoke_key(key_id: str, user=Depends(admin_user)):
        with database_connection(database_path) as db:
            if not db.execute('UPDATE admin_api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ? AND user_id = ?', (now_iso(), key_id, user['id'])).rowcount:
                raise HTTPException(404, '密钥不存在')
        return {'ok': True}

    @router.get('/v1/models')
    async def models(principal=Depends(api_principal)):
        return {'object': 'list', 'data': [{'object': 'model', **item} for item in await image_model_items()]}

    def owned_image(item_id, user_id):
        if not re.fullmatch(r'[0-9a-f]{24}', item_id):
            raise HTTPException(404, '图片不存在')
        with database_connection(database_path) as db:
            owned = db.execute('SELECT 1 FROM image_history WHERE id = ? AND user_id = ?', (item_id, user_id)).fetchone()
        if owned:
            for extension in ('png', 'jpg', 'webp'):
                path = image_dir / f'{item_id}.{extension}'
                if path.is_file():
                    return path
        raise HTTPException(404, '图片不存在')

    @router.get('/v1/images/files/{item_id}', name='admin_api_image_file')
    def image_file(item_id: str, principal=Depends(api_principal)):
        path = owned_image(item_id, principal['user']['id'])
        return FileResponse(path, filename=path.name, content_disposition_type='inline', headers={'Cache-Control': 'no-store'})

    def references(body):
        images = list(body.image_data_urls)
        if body.image_data_url:
            images.append(body.image_data_url)
        if not images:
            raise HTTPException(400, '请先添加参考图')
        return images

    async def api_generate(body, images, principal, request, request_key):
        user = principal['user']
        with admission.slot(user['id'], principal['key']):
            result = await run_generation(body, images, user, request_key)
            item = result['item']
            if body.response_format == 'url':
                data = {'url': str(request.url_for('admin_api_image_file', item_id=item['id']))}
            else:
                def read_encoded_image():
                    return base64.b64encode(owned_image(item['id'], user['id']).read_bytes()).decode('ascii')
                data = {'b64_json': await run_in_threadpool(read_encoded_image)}
            return {'created': int(datetime.fromisoformat(item['created_at']).timestamp()), 'data': [data],
                    'id': item['id'], 'credits': result['credits'], 'credits_cost': item['credits_cost'],
                    'model': item['model'], 'size': item['size'], 'resolution': item['resolution'],
                    'output_size': item.get('output_size')}

    @router.post('/v1/images/generations')
    async def api_generation(body: ApiGenerationRequest, request: Request, principal=Depends(api_principal),
                             idempotency_key: Annotated[str | None, Header()] = None):
        return await api_generate(body, [], principal, request, idempotency_key)

    @router.post('/v1/images/edits')
    async def api_edit(body: ApiEditRequest, request: Request, principal=Depends(api_principal),
                       idempotency_key: Annotated[str | None, Header()] = None):
        return await api_generate(body, references(body), principal, request, idempotency_key)

    @router.post('/api/admin/images/generations')
    async def panel_generation(body: generation_request_type, user=Depends(admin_user),
                               idempotency_key: Annotated[str | None, Header()] = None):
        with admission.slot(user['id']):
            return await run_generation(body, [], user, idempotency_key)

    @router.post('/api/admin/images/edits')
    async def panel_edit(body: edit_request_type, user=Depends(admin_user),
                         idempotency_key: Annotated[str | None, Header()] = None):
        with admission.slot(user['id']):
            return await run_generation(body, references(body), user, idempotency_key)

    return router
