from __future__ import annotations

import base64
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Annotated, Any

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "app" / "static"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
SESSION_COOKIE = "astrogate_session"
PASSWORD_ITERATIONS = 600_000


def load_local_environment() -> None:
    """Load simple KEY=value entries without overriding process environment variables."""
    env_file = ROOT_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_local_environment()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    upstream_url: str
    upstream_api_key: str
    cookie_secure: bool
    upstream_transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        configured_data_dir = os.getenv("ASTROGATE_DATA_DIR", "./data")
        data_dir = Path(configured_data_dir)
        if not data_dir.is_absolute():
            data_dir = ROOT_DIR / data_dir
        return cls(
            data_dir=data_dir,
            upstream_url=os.getenv("ASTROGATE_UPSTREAM_URL", "").strip().rstrip("/"),
            upstream_api_key=os.getenv("ASTROGATE_UPSTREAM_API_KEY", "").strip(),
            cookie_secure=os.getenv("ASTROGATE_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"},
        )


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4_000)
    model: str = Field(default="gpt-image-2", min_length=1, max_length=100)
    size: str = Field(default="1024x1024", pattern=r"^(1024x1024|1024x1536|1536x1024)$")
    quality: str = Field(default="medium", pattern=r"^(low|medium|high)$")


class ImageEditRequest(ImageGenerationRequest):
    image_data_url: str = Field(min_length=16)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database_connection(path: Path):
    connection = open_database(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(path) as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS image_history (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                size TEXT NOT NULL,
                quality TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'text-to-image',
                image_src TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS image_history_by_user ON image_history(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
            """
        )
        columns = {str(row[1]) for row in database.execute("PRAGMA table_info(image_history)").fetchall()}
        if "mode" not in columns:
            database.execute("ALTER TABLE image_history ADD COLUMN mode TEXT NOT NULL DEFAULT 'text-to-image'")


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="账号需为 3 至 32 位字母、数字、点、下划线或连字符")
    return normalized


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iteration_text))
        return hmac.compare_digest(actual_digest, expected_digest)
    except (TypeError, ValueError):
        return False


def hash_session(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def image_error_detail(payload: object) -> str:
    if not isinstance(payload, dict):
        return "上游图片服务返回错误"
    detail = payload.get("error") or payload.get("detail") or "上游图片服务返回错误"
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("error") or "上游图片服务返回错误"
    return str(detail)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_environment()
    database_path = app_settings.data_dir / "astrogate.db"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize_database(database_path)
        yield

    app = FastAPI(title="AstroGate IMG", version="0.1.0", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    def current_user(session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> dict[str, Any]:
        if not session_token:
            raise HTTPException(status_code=401, detail="请先登录")
        with database_connection(database_path) as database:
            row = database.execute(
                """
                SELECT users.id, users.username, users.created_at, sessions.expires_at
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                """,
                (hash_session(session_token),),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) <= utc_now():
                if row is not None:
                    database.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_session(session_token),))
                raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
            return public_user(row)

    def issue_session(response: Response, user_id: int) -> None:
        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(days=14)
        with database_connection(database_path) as database:
            database.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_iso(),))
            database.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (hash_session(token), user_id, utc_iso(expires_at), utc_iso()),
            )
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            max_age=14 * 24 * 60 * 60,
            httponly=True,
            secure=app_settings.cookie_secure,
            samesite="lax",
            path="/",
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "upstream_configured": bool(app_settings.upstream_url and app_settings.upstream_api_key)}

    @app.get("/api/config")
    async def public_config() -> dict[str, object]:
        return {"upstream_configured": bool(app_settings.upstream_url and app_settings.upstream_api_key)}

    @app.post("/api/auth/register", status_code=201)
    async def register(body: Credentials, response: Response) -> dict[str, object]:
        username = validate_username(body.username)
        with database_connection(database_path) as database:
            try:
                cursor = database.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, hash_password(body.password), utc_iso()),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="该账号已注册") from exc
            user_id = int(cursor.lastrowid)
            user = database.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        issue_session(response, user_id)
        return {"user": public_user(user)}

    @app.post("/api/auth/login")
    async def login(body: Credentials, response: Response) -> dict[str, object]:
        username = validate_username(body.username)
        with database_connection(database_path) as database:
            row = database.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        issue_session(response, int(row["id"]))
        return {"user": public_user(row)}

    @app.post("/api/auth/logout")
    async def logout(response: Response, session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> dict[str, bool]:
        if session_token:
            with database_connection(database_path) as database:
                database.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_session(session_token),))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, object]:
        return {"user": user}

    @app.get("/api/images/history")
    async def image_history(user: dict[str, Any] = Depends(current_user)) -> dict[str, object]:
        with database_connection(database_path) as database:
            rows = database.execute(
                """
                SELECT id, prompt, model, size, quality, mode, image_src, status, error_message, created_at
                FROM image_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 60
                """,
                (user["id"],),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.delete("/api/images/history/{item_id}")
    async def delete_history(item_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
        with database_connection(database_path) as database:
            cursor = database.execute("DELETE FROM image_history WHERE id = ? AND user_id = ?", (item_id, user["id"]))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"ok": True}

    @app.post("/api/images/generations")
    async def generate_image(body: ImageGenerationRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, object]:
        if not app_settings.upstream_url or not app_settings.upstream_api_key:
            raise HTTPException(status_code=503, detail="服务尚未配置上游图片接口")

        request_id = secrets.token_hex(12)
        payload = {"model": body.model.strip(), "prompt": body.prompt.strip(), "n": 1, "size": body.size, "quality": body.quality}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(150.0, connect=15.0),
                transport=app_settings.upstream_transport,
            ) as client:
                upstream_response = await client.post(
                    f"{app_settings.upstream_url}/v1/images/generations",
                    headers={"Authorization": f"Bearer {app_settings.upstream_api_key}"},
                    json=payload,
                )
            response_payload = upstream_response.json()
            if upstream_response.is_error:
                raise HTTPException(
                    status_code=429 if upstream_response.status_code == 429 else 502,
                    detail=image_error_detail(response_payload),
                )
            images = response_payload.get("data") if isinstance(response_payload, dict) else None
            first_image = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else {}
            image_src = str(first_image.get("url") or "").strip()
            if not image_src and first_image.get("b64_json"):
                image_src = f"data:image/png;base64,{first_image['b64_json']}"
            if not image_src:
                raise HTTPException(status_code=502, detail="上游未返回图片数据")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="无法连接上游图片服务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="上游返回了无法识别的数据") from exc

        created_at = utc_iso()
        with database_connection(database_path) as database:
            database.execute(
                """
                INSERT INTO image_history (id, user_id, prompt, model, size, quality, mode, image_src, status, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'text-to-image', ?, 'succeeded', NULL, ?)
                """,
                (request_id, user["id"], body.prompt.strip(), body.model.strip(), body.size, body.quality, image_src, created_at),
            )
        return {
            "item": {
                "id": request_id,
                "prompt": body.prompt.strip(),
                "model": body.model.strip(),
                "size": body.size,
                "quality": body.quality,
                "mode": "text-to-image",
                "image_src": image_src,
                "status": "succeeded",
                "created_at": created_at,
            }
        }

    @app.post("/api/images/edits")
    async def edit_image(body: ImageEditRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, object]:
        if not app_settings.upstream_url or not app_settings.upstream_api_key:
            raise HTTPException(status_code=503, detail="服务尚未配置上游图片接口")
        header, separator, encoded_image = body.image_data_url.partition(",")
        if not separator or not header.lower().startswith("data:image/") or ";base64" not in header.lower():
            raise HTTPException(status_code=400, detail="参考图格式无效")
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(status_code=400, detail="参考图仅支持 PNG、JPG 和 WebP")
        try:
            image_bytes = base64.b64decode(encoded_image, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="参考图数据无效") from exc
        if not image_bytes:
            raise HTTPException(status_code=400, detail="参考图不能为空")
        if len(image_bytes) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="参考图不能超过 50MB")
        valid_signature = (
            (mime_type == "image/png" and image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime_type == "image/jpeg" and image_bytes.startswith(b"\xff\xd8\xff"))
            or (mime_type == "image/webp" and image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP")
        )
        if not valid_signature:
            raise HTTPException(status_code=400, detail="参考图内容与文件格式不匹配")
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime_type]
        payload = {
            "prompt": body.prompt.strip(),
            "model": body.model.strip(),
            "n": 1,
            "size": body.size,
            "quality": body.quality,
            "response_format": "b64_json",
            "images": [{"b64_json": encoded_image, "filename": f"reference.{extension}", "mime_type": mime_type}],
        }
        request_id = secrets.token_hex(12)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(150.0, connect=15.0),
                transport=app_settings.upstream_transport,
            ) as client:
                upstream_response = await client.post(
                    f"{app_settings.upstream_url}/v1/images/edits",
                    headers={"Authorization": f"Bearer {app_settings.upstream_api_key}"},
                    json=payload,
                )
            response_payload = upstream_response.json()
            if upstream_response.is_error:
                raise HTTPException(
                    status_code=429 if upstream_response.status_code == 429 else 502,
                    detail=image_error_detail(response_payload),
                )
            images = response_payload.get("data") if isinstance(response_payload, dict) else None
            first_image = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else {}
            image_src = str(first_image.get("url") or "").strip()
            if not image_src and first_image.get("b64_json"):
                image_src = f"data:image/png;base64,{first_image['b64_json']}"
            if not image_src:
                raise HTTPException(status_code=502, detail="上游未返回图片数据")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="无法连接上游图片服务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="上游返回了无法识别的数据") from exc

        created_at = utc_iso()
        with database_connection(database_path) as database:
            database.execute(
                """
                INSERT INTO image_history (id, user_id, prompt, model, size, quality, mode, image_src, status, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'image-to-image', ?, 'succeeded', NULL, ?)
                """,
                (request_id, user["id"], body.prompt.strip(), body.model.strip(), body.size, body.quality, image_src, created_at),
            )
        return {
            "item": {
                "id": request_id,
                "prompt": body.prompt.strip(),
                "model": body.model.strip(),
                "size": body.size,
                "quality": body.quality,
                "mode": "image-to-image",
                "image_src": image_src,
                "status": "succeeded",
                "created_at": created_at,
            }
        }

    return app


app = create_app()
