"""Private gateway integration. Account renewal remains owned by AstroGate."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image

MAX_BODY = 72 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
ASSET_LIFETIME = 7 * 24 * 3600
MANAGED_BY = "astrogate-gateway"


def create_gateway_router(config) -> APIRouter:
    router = APIRouter()
    lock = asyncio.Lock()
    assets = config.data_dir / "gateway-assets"
    status = {"running": False, "synced": 0, "skipped": 0, "removed": 0, "last_success": None}

    @contextmanager
    def job_database():
        config.data_dir.mkdir(parents=True, exist_ok=True)
        path = config.data_dir / "gateway-bridge.sqlite3"
        path.touch(mode=0o600, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS image_jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created REAL NOT NULL)")
            with db:
                yield db
        finally:
            db.close()

    def reserve_job(request_id, payload):
        if request_id is None:
            return None
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{64}", request_id):
            raise HTTPException(400, "Invalid gateway request ID")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with job_database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM image_jobs WHERE created < ?", (time.time() - ASSET_LIFETIME,))
            row = db.execute("SELECT fingerprint,status,result FROM image_jobs WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise HTTPException(409, "Gateway request ID was already used for another request")
                if row[1] != "succeeded":
                    raise HTTPException(409, "This image request is pending or its outcome needs verification")
                result = json.loads(row[2])
                for item in result["data"]:
                    filename = item.pop("filename")
                    if not re.fullmatch(r"[0-9a-f]{48}\.(?:png|jpg|webp)", filename) or not (assets / filename).is_file():
                        raise HTTPException(409, "The image download has expired; start a new request")
                    item["b64_json"] = base64.b64encode((assets / filename).read_bytes()).decode()
                return result
            db.execute("INSERT INTO image_jobs VALUES(?,?,'pending',NULL,?)", (request_id, fingerprint, time.time()))
        return None

    def complete_job(request_id, result):
        if request_id is None:
            return
        metadata = {"created": result["created"], "data": [{key: value for key, value in item.items() if key != "b64_json"} for item in result["data"]]}
        for item in metadata["data"]:
            item["filename"] = item["url"].rsplit("/", 1)[1]
        with job_database() as db:
            db.execute("UPDATE image_jobs SET status='succeeded',result=? WHERE id=?", (json.dumps(metadata), request_id))

    def authenticate(authorization):
        expected = config.gateway_bridge_key
        supplied = (authorization or "").removeprefix("Bearer ")
        if len(expected) < 32 or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "Gateway authorization required")
        if not config.upstream_url or not config.upstream_api_key:
            raise HTTPException(503, "Image upstream is not configured")

    async def bounded_json(request, limit):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise HTTPException(413, "Request is too large")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON") from None
        if not isinstance(value, dict):
            raise HTTPException(400, "JSON object required")
        return value

    def client(timeout=840):
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10),
                                 transport=config.upstream_transport, trust_env=False, follow_redirects=False,
                                 headers={"Authorization": "Bearer " + config.upstream_api_key})

    async def upstream_json(http, method, path, body=None, limit=MAX_BODY):
        try:
            async with http.stream(method, config.upstream_url + path, json=body) as response:
                if not 200 <= response.status_code < 300:
                    raise HTTPException(429 if response.status_code == 429 else 502, "Image upstream request failed")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise HTTPException(502, "Image upstream response is too large")
            return json.loads(raw)
        except (httpx.HTTPError, ValueError, UnicodeError):
            # Provider bodies may contain credentials; never return or log them.
            raise HTTPException(502, "Image upstream request did not complete") from None

    def image_bytes(value):
        try:
            data = base64.b64decode(value, validate=True)
            if not data or len(data) > MAX_OUTPUT:
                raise ValueError()
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 24_000_000:
                    raise ValueError()
                extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
                if not extension:
                    raise ValueError()
                image.verify()
            return data, extension
        except (ValueError, TypeError, binascii.Error, OSError, Image.DecompressionBombError):
            raise HTTPException(502, "Image upstream returned an invalid image") from None

    def image_response(result, request_id, stream, edit):
        headers = {"X-Request-ID": request_id} if request_id else {}
        if stream:
            def events():
                for index, item in enumerate(result["data"]):
                    event = {"type": "image_edit.completed" if edit else "image_generation.completed", "created_at": result["created"], "index": index, **item}
                    yield "data: " + json.dumps(event) + "\n\n"
            return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
        return JSONResponse(result, headers=headers)

    def save_asset(data, extension):
        origin = config.gateway_public_origin.rstrip("/")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?", origin):
            raise HTTPException(503, "Gateway asset origin is not configured")
        assets.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        for path in assets.iterdir():
            if path.is_file() and path.stat().st_mtime < now - ASSET_LIFETIME:
                path.unlink(missing_ok=True)
        filename = secrets.token_hex(24) + "." + extension
        (assets / filename).write_bytes(data)
        return origin + "/gateway-assets/" + filename

    async def generate(request, authorization, edit):
        authenticate(authorization)
        body = await bounded_json(request, MAX_BODY)
        prompt, model = body.get("prompt"), body.get("model", "gpt-image-2")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise HTTPException(400, "A prompt of at most 16000 characters is required")
        if model not in {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"}:
            raise HTTPException(400, "Unsupported image model")
        count = body.get("n", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 4:
            raise HTTPException(400, "n must be between 1 and 4")
        if "stream" in body and not isinstance(body["stream"], bool):
            raise HTTPException(400, "stream must be a boolean")
        if body.get("mask") is not None:
            raise HTTPException(400, "Image masks are not supported by this image node")
        payload = {"model": model, "prompt": prompt, "n": count, "stream": False, "response_format": "b64_json"}
        for field in ("size", "quality"):
            if field in body:
                if not isinstance(body[field], str) or len(body[field]) > 64:
                    raise HTTPException(400, "Invalid image option")
                payload[field] = body[field]
        if edit:
            images = body.get("images")
            if not isinstance(images, list) or not 1 <= len(images) <= 8:
                raise HTTPException(400, "One to eight reference images are required")
            converted = []
            for item in images:
                if not isinstance(item, dict):
                    raise HTTPException(400, "Invalid reference image")
                value = item.get("image_url", "")
                if isinstance(value, str) and re.match(r"^data:image/(?:png|jpeg|webp);base64,", value):
                    encoded = value.split(",", 1)[1]
                else:
                    encoded = item.get("b64_json")
                if not isinstance(encoded, str):
                    raise HTTPException(400, "Upload reference images as base64 data URLs")
                data, extension = image_bytes(encoded)
                converted.append({"b64_json": base64.b64encode(data).decode(), "filename": "reference." + extension,
                                  "mime_type": "image/" + ("jpeg" if extension == "jpg" else extension)})
            payload["images"] = converted
        request_id = body.get("gateway_request_id")
        previous = reserve_job(request_id, payload)
        if previous:
            return image_response(previous, request_id, body.get("stream") is True, edit)
        async with client() as http:
            result = await upstream_json(http, "POST", "/v1/images/" + ("edits" if edit else "generations"), payload)
        items = result.get("data") if isinstance(result, dict) else None
        if not isinstance(items, list) or not items or len(items) > count:
            raise HTTPException(502, "Image upstream returned no image")
        output = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("b64_json"), str):
                raise HTTPException(502, "Image upstream returned no image")
            data, extension = image_bytes(item["b64_json"])
            output.append({"b64_json": base64.b64encode(data).decode(), "url": save_asset(data, extension),
                           "revised_prompt": str(item.get("revised_prompt") or prompt)[:16000], "output_format": "jpeg" if extension == "jpg" else extension})
        # Gateway usage accounting owns these requests; no IMG customer credits
        # or cookies are consumed and no operator token reaches the response.
        result = {"created": int(time.time()), "data": output}
        complete_job(request_id, result)
        return image_response(result, request_id, body.get("stream") is True, edit)

    @router.get("/internal/astrogate/v1/models")
    async def models(authorization: str | None = Header(default=None)):
        authenticate(authorization)
        async with client(timeout=35) as http:
            result = await upstream_json(http, "GET", "/v1/models", limit=1024 * 1024)
        supported = {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"}
        items = result.get("data", []) if isinstance(result, dict) else []
        return {"object": "list", "data": [{"id": item["id"], "object": "model", "owned_by": "astrogate-img"} for item in items if isinstance(item, dict) and item.get("id") in supported]}

    @router.post("/internal/astrogate/v1/images/generations")
    async def generate_image(request: Request, authorization: str | None = Header(default=None)):
        return await generate(request, authorization, False)

    @router.post("/internal/astrogate/v1/images/edits")
    async def edit_image(request: Request, authorization: str | None = Header(default=None)):
        return await generate(request, authorization, True)

    @router.get("/internal/astrogate/v1/images/jobs/{request_id}")
    async def get_image_job(request_id: str, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        if not re.fullmatch(r"[0-9a-f]{64}", request_id):
            raise HTTPException(400, "Invalid gateway request ID")
        database = config.data_dir / "gateway-bridge.sqlite3"
        if not database.is_file():
            return {"status": "missing"}
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            row = db.execute("SELECT status,result FROM image_jobs WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return {"status": "missing"}
        if row[0] != "succeeded":
            return {"status": "pending" if row[0] == "pending" else "unknown"}
        try:
            result = json.loads(row[1])
            items = result["data"]
            if not isinstance(items, list) or not 1 <= len(items) <= 4:
                return {"status": "unavailable"}
            size = 0
            for item in items:
                filename = item.pop("filename")
                if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{48}\.(?:png|jpg|webp)", filename):
                    return {"status": "unavailable"}
                path = assets / filename
                if path.is_symlink() or not path.is_file() or path.stat().st_mtime < time.time() - ASSET_LIFETIME:
                    return {"status": "expired"}
                with path.open("rb") as image:
                    data = image.read(MAX_OUTPUT - size + 1)
                if not data or len(data) > MAX_OUTPUT - size:
                    return {"status": "unavailable"}
                size += len(data)
                item["b64_json"] = base64.b64encode(data).decode()
        except (ValueError, TypeError, KeyError, OSError, AttributeError):
            return {"status": "unavailable"}
        return {"status": "succeeded", "result": result}

    @router.get("/gateway-assets/{filename}")
    async def download_asset(filename: str):
        if not re.fullmatch(r"[0-9a-f]{48}\.(?:png|jpg|webp)", filename):
            raise HTTPException(404, "Image not found")
        path = assets / filename
        if not path.is_file() or path.is_symlink() or path.stat().st_mtime < time.time() - ASSET_LIFETIME:
            raise HTTPException(404, "Image not found")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"})

    @router.get("/internal/astrogate/accounts/status")
    async def sync_status(authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return dict(status)

    @router.post("/internal/astrogate/accounts/sync")
    async def sync_accounts(request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        body = await bounded_json(request, 8 * 1024 * 1024)
        accounts = body.get("accounts")
        if not isinstance(accounts, list) or len(accounts) > 1000 or body.get("complete") is not True:
            raise HTTPException(400, "A complete account snapshot of at most 1000 records is required")
        desired = {}
        for item in accounts:
            if not isinstance(item, dict):
                raise HTTPException(400, "Invalid account snapshot")
            source_id, token = item.get("source_id"), item.get("access_token")
            if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", source_id):
                raise HTTPException(400, "Invalid source account")
            if source_id in desired or not isinstance(token, str) or not 1 <= len(token) <= 32768:
                raise HTTPException(400, "Invalid account snapshot")
            payload = {"access_token": token, "refresh_token": "", "password": "", "id_token": "", "source_type": "sub2api",
                       "astrogate_managed_by": MANAGED_BY, "astrogate_source_id": source_id}
            for field in ("email", "account_id", "user_id", "expires_at"):
                value = item.get(field)
                if value is not None:
                    if not isinstance(value, str) or len(value) > 512:
                        raise HTTPException(400, "Invalid account metadata")
                    payload[field] = value
            desired[source_id] = payload
        if lock.locked():
            raise HTTPException(409, "Authorization synchronization is already running")
        async with lock:
            status["running"] = True
            try:
                async with client(timeout=300) as http:
                    snapshot = await upstream_json(http, "GET", "/api/accounts", limit=16 * 1024 * 1024)
                    existing = snapshot.get("items") if isinstance(snapshot, dict) else None
                    if not isinstance(existing, list):
                        raise HTTPException(502, "Invalid account pool response")
                    managed = [row for row in existing if isinstance(row, dict) and row.get("astrogate_managed_by") == MANAGED_BY]
                    current = {row.get("astrogate_source_id"): row for row in managed}
                    changed = [row for source_id, row in desired.items()
                               if current.get(source_id, {}).get("access_token") != row["access_token"]
                               or current.get(source_id, {}).get("refresh_token")]
                    for offset in range(0, len(changed), 10):
                        await upstream_json(http, "POST", "/api/accounts", {"accounts": changed[offset:offset + 10]}, limit=16 * 1024 * 1024)
                    retained = {row["access_token"] for row in desired.values()}
                    obsolete = list({row.get("access_token") for row in managed if row.get("access_token") not in retained} - {None, ""})
                    if obsolete:
                        await upstream_json(http, "DELETE", "/api/accounts", {"tokens": obsolete}, limit=16 * 1024 * 1024)
                status.update(synced=len(changed), skipped=len(desired) - len(changed), removed=len(obsolete), last_success=int(time.time()))
                return {key: value for key, value in status.items() if key != "running"}
            finally:
                status["running"] = False

    return router
