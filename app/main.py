from __future__ import annotations

import base64
import binascii
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock
import time
from typing import Annotated, Any, Callable
from urllib.parse import urlparse

import httpx
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

from app.mail import send_registration_code

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "app" / "static"
SESSION_COOKIE = "astrogate_session"
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_REFERENCES = 8
PASSWORD_ITERATIONS = 600_000
PRICE = {"credits_per_image": 1, "rmb_per_image": "0.05"}


def load_local_environment() -> None:
    env_file = ROOT_DIR / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_environment()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    upstream_url: str
    upstream_api_key: str
    cookie_secure: bool
    upstream_transport: httpx.AsyncBaseTransport | None = None
    admin_username: str = ""
    admin_password: str = ""
    signup_credits: int = 0
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_security: str = "none"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_sender: str = "noreply@astrogate.sbs"
    email_code_secret: str = ""
    mail_sender: Callable[[str, str], None] | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        data_dir = Path(os.getenv("ASTROGATE_DATA_DIR", "./data"))
        return cls(
            data_dir=data_dir if data_dir.is_absolute() else ROOT_DIR / data_dir,
            upstream_url=os.getenv("ASTROGATE_UPSTREAM_URL", "").strip().rstrip("/"),
            upstream_api_key=os.getenv("ASTROGATE_UPSTREAM_API_KEY", "").strip(),
            cookie_secure=os.getenv("ASTROGATE_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
            admin_username=os.getenv("ASTROGATE_ADMIN_USERNAME", "").strip(),
            admin_password=os.getenv("ASTROGATE_ADMIN_PASSWORD", ""),
            signup_credits=max(0, int(os.getenv("ASTROGATE_SIGNUP_CREDITS", "0"))),
            smtp_host=os.getenv("ASTROGATE_SMTP_HOST", "").strip(),
            smtp_port=int(os.getenv("ASTROGATE_SMTP_PORT", "25")),
            smtp_security=os.getenv("ASTROGATE_SMTP_SECURITY", "none").strip().lower(),
            smtp_username=os.getenv("ASTROGATE_SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("ASTROGATE_SMTP_PASSWORD", ""),
            smtp_sender=os.getenv("ASTROGATE_SMTP_SENDER", "noreply@astrogate.sbs").strip(),
            email_code_secret=os.getenv("ASTROGATE_EMAIL_CODE_SECRET", ""),
        )


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", value):
            raise ValueError("账号需为 3 至 32 位字母、数字、点、下划线或连字符")
        return value


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=254, validation_alias=AliasChoices("identifier", "email", "username"))
    password: str = Field(min_length=8, max_length=128)


class EmailCodeRequest(BaseModel):
    email: EmailStr = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return str(value).lower()


class EmailRegistrationRequest(EmailCodeRequest):
    password: str = Field(min_length=8, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: str = Field(default="gpt-image-2", pattern=r"^gpt-image-2$")
    size: str = Field(default="1024x1024", pattern=r"^(1024x1024|1024x1536|1536x1024)$")
    quality: str = Field(default="medium", pattern=r"^(low|medium|high)$")

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写画面描述")
        return value.strip()


class ImageEditRequest(ImageGenerationRequest):
    image_data_urls: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    image_data_url: str | None = None  # Compatibility with the first client release.


class RedeemRequest(BaseModel):
    code: str = Field(min_length=8, max_length=120)


class CreditAdjustment(BaseModel):
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(default="管理员调整", min_length=1, max_length=160)


class CodeCreation(BaseModel):
    credits: int = Field(ge=1, le=1_000_000)
    count: int = Field(default=1, ge=1, le=50)
    expires_days: int = Field(default=90, ge=1, le=3650)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return algorithm == "pbkdf2_sha256" and hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, binascii.Error):
        return False


@contextmanager
def database_connection(path: Path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def ledger_entry(db, user_id: int, amount: int, kind: str, description: str, operation: str) -> int:
    balance = db.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    db.execute(
        "INSERT INTO credit_ledger (user_id, amount, balance_after, kind, description, operation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, amount, balance, kind, description, operation, now_iso()),
    )
    return balance


def initialize_database(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 0 CHECK(credits >= 0), is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS image_history (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                prompt TEXT NOT NULL, model TEXT NOT NULL, size TEXT NOT NULL, quality TEXT NOT NULL,
                image_src TEXT, status TEXT NOT NULL, error_message TEXT, created_at TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'text-to-image', reference_count INTEGER NOT NULL DEFAULT 0,
                credits_cost INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS credit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, kind TEXT NOT NULL,
                description TEXT NOT NULL, operation TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS redemption_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT NOT NULL UNIQUE, suffix TEXT NOT NULL,
                credits INTEGER NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, redeemed_by INTEGER REFERENCES users(id), redeemed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS generation_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, error_message TEXT,
                UNIQUE(user_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS email_verifications (
                email TEXT PRIMARY KEY COLLATE NOCASE, code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL, requested_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS email_verification_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, requested_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS verification_sends_by_email ON email_verification_sends(email, requested_at);
            CREATE INDEX IF NOT EXISTS image_history_by_user ON image_history(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS ledger_by_user ON credit_ledger(user_id, id DESC);
        """)
        migrations = {
            "users": {"credits": "INTEGER NOT NULL DEFAULT 0", "is_admin": "INTEGER NOT NULL DEFAULT 0", "email": "TEXT COLLATE NOCASE", "email_verified_at": "TEXT"},
            "image_history": {"mode": "TEXT NOT NULL DEFAULT 'text-to-image'", "reference_count": "INTEGER NOT NULL DEFAULT 0", "credits_cost": "INTEGER NOT NULL DEFAULT 0"},
        }
        for table, fields in migrations.items():
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in fields.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_by_email ON users(email COLLATE NOCASE) WHERE email IS NOT NULL")
        if settings.admin_username and settings.admin_password:
            Credentials(username=settings.admin_username, password=settings.admin_password)
            admin = db.execute("SELECT id FROM users WHERE username = ?", (settings.admin_username,)).fetchone()
            if admin:
                db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin[0],))
            else:
                db.execute("INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, 1)",
                           (settings.admin_username, hash_password(settings.admin_password), now_iso()))
        # One application worker owns jobs. Refund reservations interrupted by a restart.
        for job in db.execute("SELECT id, user_id FROM generation_jobs WHERE status = 'pending'").fetchall():
            db.execute("UPDATE users SET credits = credits + 1 WHERE id = ?", (job["user_id"],))
            ledger_entry(db, job["user_id"], 1, "refund", "服务重启，积分已退回", f"refund:{job['id']}")
            db.execute("UPDATE generation_jobs SET status = 'failed', error_message = '服务重启，积分已退回' WHERE id = ?", (job["id"],))


def public_user(row) -> dict[str, Any]:
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"],
            "email": row["email"], "email_verified": bool(row["email_verified_at"]),
            "display_name": row["email"].split("@")[0] if row["email"] else row["username"],
            "credits": row["credits"], "is_admin": bool(row["is_admin"])}


def image_error_detail(payload: object) -> str:
    if not isinstance(payload, dict):
        return "上游图片服务返回错误"
    detail = payload.get("error") or payload.get("detail") or "上游图片服务返回错误"
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("error") or "上游图片服务返回错误"
    return str(detail)[:500]


def image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise HTTPException(400, "图片内容无效，仅支持 PNG、JPG、WebP")


def decode_reference(value: str) -> tuple[dict[str, str], int]:
    header, separator, encoded = value.partition(",")
    types = {"data:image/png;base64": "png", "data:image/jpeg;base64": "jpg", "data:image/webp;base64": "webp"}
    if not separator or header.lower() not in types:
        raise HTTPException(400, "参考图仅支持 PNG、JPG、WebP")
    if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise HTTPException(413, "参考图总大小不能超过 50MB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "参考图数据无效") from exc
    extension = image_extension(data)
    if extension != types[header.lower()]:
        raise HTTPException(400, "参考图内容与文件格式不匹配")
    return {"b64_json": encoded, "filename": f"reference.{extension}", "mime_type": header[5:].split(";")[0].lower()}, len(data)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_environment()
    database_path = config.data_dir / "astrogate.db"
    image_dir = config.data_dir / "images"
    attempts: dict[str, deque] = defaultdict(deque)
    attempt_lock = Lock()
    verification_secret = config.email_code_secret

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal verification_secret
        initialize_database(database_path, config)
        image_dir.mkdir(parents=True, exist_ok=True)
        if not verification_secret:
            secret_file = config.data_dir / ".email-code-secret"
            if not secret_file.exists():
                descriptor = os.open(secret_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w") as handle:
                    handle.write(secrets.token_urlsafe(48))
            verification_secret = secret_file.read_text().strip()
        yield

    app = FastAPI(title="AstroGate IMG", version="0.3.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "DELETE", "PUT", "PATCH"} and origin:
            if urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请求来源无效"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def rate_limit(request: Request, kind: str, limit: int, period: int) -> None:
        key = f"{kind}:{request.client.host if request.client else 'unknown'}"
        with attempt_lock:
            queue = attempts[key]
            now = time.monotonic()
            while queue and queue[0] < now - period:
                queue.popleft()
            if len(queue) >= limit:
                raise HTTPException(429, "操作过于频繁，请稍后再试")
            queue.append(now)

    def current_user(session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None):
        if not session_token:
            raise HTTPException(401, "请先登录")
        with database_connection(database_path) as db:
            row = db.execute("SELECT users.*, sessions.expires_at FROM sessions JOIN users ON users.id = sessions.user_id WHERE token_hash = ?",
                             (hash_token(session_token),)).fetchone()
            if not row or row["expires_at"] <= now_iso():
                raise HTTPException(401, "登录已过期，请重新登录")
            return public_user(row)

    def admin_user(user=Depends(current_user)):
        if not user["is_admin"]:
            raise HTTPException(403, "需要管理员权限")
        return user

    def issue_session(response: Response, user_id: int):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        with database_connection(database_path) as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (hash_token(token), user_id, expires, now_iso()))
        response.set_cookie(SESSION_COOKIE, token, max_age=1209600, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "home.html", headers={"Cache-Control": "no-cache"})

    @app.get("/studio")
    @app.get("/login")
    @app.get("/register")
    @app.get("/wallet")
    @app.get("/admin")
    def studio():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health():
        return {"ok": True, "upstream_configured": bool(config.upstream_url and config.upstream_api_key)}

    @app.get("/api/config")
    def public_config():
        return {"upstream_configured": bool(config.upstream_url and config.upstream_api_key), "pricing": PRICE,
                "email_registration": True, "max_reference_images": MAX_REFERENCES, "max_reference_bytes": MAX_IMAGE_BYTES}

    def code_digest(email: str, code: str) -> str:
        return hmac.new(verification_secret.encode(), f"register:{email}:{code}".encode(), hashlib.sha256).hexdigest()

    @app.post("/api/auth/email-code")
    def request_email_code(body: EmailCodeRequest, request: Request):
        if not config.smtp_host and config.mail_sender is None:
            raise HTTPException(503, "邮件服务暂未配置，请稍后再试")
        rate_limit(request, "email-code", 12, 3600)
        now = datetime.now(timezone.utc)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM users WHERE email = ? OR username = ?", (body.email, body.email)).fetchone():
                raise HTTPException(409, "该邮箱已注册，请直接登录")
            existing = db.execute("SELECT requested_at FROM email_verifications WHERE email = ?", (body.email,)).fetchone()
            if existing and (now - datetime.fromisoformat(existing["requested_at"])).total_seconds() < 60:
                raise HTTPException(429, "验证码已发送，请 60 秒后再试")
            cutoff = (now - timedelta(hours=1)).isoformat()
            recent = db.execute("SELECT COUNT(*) FROM email_verification_sends WHERE email = ? AND requested_at >= ?", (body.email, cutoff)).fetchone()[0]
            if recent >= 5:
                raise HTTPException(429, "该邮箱发送次数过多，请稍后再试")
            db.execute("INSERT INTO email_verifications (email, code_hash, expires_at, requested_at, attempts) VALUES (?, ?, ?, ?, 0) ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at, requested_at = excluded.requested_at, attempts = 0",
                       (body.email, code_digest(body.email, code), (now + timedelta(minutes=10)).isoformat(), now.isoformat()))
            db.execute("INSERT INTO email_verification_sends (email, requested_at) VALUES (?, ?)", (body.email, now.isoformat()))
            db.execute("DELETE FROM email_verification_sends WHERE requested_at < ?", ((now - timedelta(days=2)).isoformat(),))
        try:
            if config.mail_sender is not None:
                config.mail_sender(body.email, code)
            else:
                send_registration_code(config, body.email, code)
        except Exception as exc:
            # SMTP may have accepted DATA before a connection fails. Keep the code
            # valid so a message that did arrive can still be used.
            raise HTTPException(502, "邮件暂时未能发送，请稍后重试；若已收到邮件，可继续使用验证码") from exc
        return {"ok": True, "expires_in": 600, "resend_after": 60}

    @app.post("/api/auth/register", status_code=201)
    def register(body: EmailRegistrationRequest, response: Response, request: Request):
        rate_limit(request, "register", 10, 3600)
        password_hash = hash_password(body.password)
        error = None
        user = None
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM users WHERE email = ? OR username = ?", (body.email, body.email)).fetchone():
                raise HTTPException(409, "该邮箱已注册，请直接登录")
            verification = db.execute("SELECT * FROM email_verifications WHERE email = ?", (body.email,)).fetchone()
            if not verification or verification["expires_at"] <= now_iso() or verification["attempts"] >= 5:
                error = "验证码无效或已过期，请重新获取"
            elif not hmac.compare_digest(verification["code_hash"], code_digest(body.email, body.code)):
                db.execute("UPDATE email_verifications SET attempts = attempts + 1 WHERE email = ?", (body.email,))
                error = "验证码不正确，请检查后重试"
            else:
                cursor = db.execute("INSERT INTO users (username, email, email_verified_at, password_hash, created_at, credits) VALUES (?, ?, ?, ?, ?, ?)",
                                    (body.email, body.email, now_iso(), password_hash, now_iso(), config.signup_credits))
                user_id = cursor.lastrowid
                if config.signup_credits:
                    ledger_entry(db, user_id, config.signup_credits, "welcome", "注册赠送", f"welcome:{user_id}")
                db.execute("DELETE FROM email_verifications WHERE email = ?", (body.email,))
                user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if error:
            raise HTTPException(400, error)
        issue_session(response, user["id"])
        return {"user": public_user(user)}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, response: Response, request: Request):
        rate_limit(request, "login", 25, 60)
        identifier = body.identifier.strip().lower()
        with database_connection(database_path) as db:
            row = db.execute("SELECT * FROM users WHERE email = ? OR username = ?", (identifier, identifier)).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "账号或密码错误")
        issue_session(response, row["id"])
        return {"user": public_user(row)}

    @app.post("/api/auth/logout")
    def logout(response: Response, session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None):
        with database_connection(database_path) as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(session_token or ""),))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user=Depends(current_user)):
        return {"user": user}

    @app.get("/api/wallet")
    def wallet(user=Depends(current_user)):
        with database_connection(database_path) as db:
            rows = db.execute("SELECT amount, balance_after, kind, description, created_at FROM credit_ledger WHERE user_id = ? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
        return {"credits": user["credits"], "pricing": PRICE, "ledger": [dict(row) for row in rows]}

    @app.post("/api/wallet/redeem")
    def redeem(body: RedeemRequest, request: Request, user=Depends(current_user)):
        rate_limit(request, "redeem", 20, 60)
        code = re.sub(r"\s+", "", body.code).upper()
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM redemption_codes WHERE code_hash = ?", (hash_token(code),)).fetchone()
            if not row or row["redeemed_by"] is not None or row["expires_at"] <= now_iso():
                raise HTTPException(400, "兑换码无效、已使用或已过期")
            db.execute("UPDATE redemption_codes SET redeemed_by = ?, redeemed_at = ? WHERE id = ?", (user["id"], now_iso(), row["id"]))
            db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (row["credits"], user["id"]))
            balance = ledger_entry(db, user["id"], row["credits"], "redeem", "兑换码充值", f"redeem:{row['id']}")
        return {"credits": balance, "added": row["credits"]}

    @app.get("/api/admin/users")
    def list_users(q: str = "", user=Depends(admin_user)):
        with database_connection(database_path) as db:
            rows = db.execute("SELECT * FROM users WHERE username LIKE ? OR email LIKE ? ORDER BY id DESC LIMIT 100", (f"%{q[:254]}%", f"%{q[:254]}%" )).fetchall()
            stats = db.execute("SELECT COUNT(*) AS users, COALESCE(SUM(credits), 0) AS credits FROM users").fetchone()
        return {"items": [public_user(row) for row in rows], "stats": dict(stats)}

    @app.post("/api/admin/users/{user_id}/credits")
    def adjust_credits(user_id: int, body: CreditAdjustment, user=Depends(admin_user)):
        if not body.amount:
            raise HTTPException(400, "调整积分不能为 0")
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
            if not target:
                raise HTTPException(404, "用户不存在")
            if target["credits"] + body.amount < 0:
                raise HTTPException(400, "调整后积分不能为负数")
            db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (body.amount, user_id))
            balance = ledger_entry(db, user_id, body.amount, "adjustment", body.reason.strip(), f"admin:{user['id']}:{secrets.token_hex(12)}")
        return {"credits": balance}

    @app.get("/api/admin/codes")
    def list_codes(user=Depends(admin_user)):
        with database_connection(database_path) as db:
            rows = db.execute("SELECT c.id, c.suffix, c.credits, c.created_at, c.expires_at, c.redeemed_at, u.username AS redeemed_username FROM redemption_codes c LEFT JOIN users u ON c.redeemed_by = u.id ORDER BY c.id DESC LIMIT 100").fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.post("/api/admin/codes")
    def create_codes(body: CodeCreation, user=Depends(admin_user)):
        expires = (datetime.now(timezone.utc) + timedelta(days=body.expires_days)).isoformat()
        codes = []
        with database_connection(database_path) as db:
            for _ in range(body.count):
                raw = secrets.token_hex(12).upper()
                code = "AGI-" + "-".join(raw[i:i+6] for i in range(0, 24, 6))
                db.execute("INSERT INTO redemption_codes (code_hash, suffix, credits, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (hash_token(code), code[-6:], body.credits, user["id"], now_iso(), expires))
                codes.append(code)
        return {"codes": codes, "credits_each": body.credits, "expires_at": expires}

    @app.get("/api/images/history")
    def image_history(user=Depends(current_user)):
        with database_connection(database_path) as db:
            rows = db.execute("SELECT id, prompt, model, size, quality, mode, reference_count, credits_cost, image_src, status, created_at FROM image_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 60", (user["id"],)).fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/images/files/{item_id}")
    def image_file(item_id: str, user=Depends(current_user)):
        with database_connection(database_path) as db:
            item = db.execute("SELECT id FROM image_history WHERE id = ? AND user_id = ?", (item_id, user["id"])).fetchone()
        if not item or not re.fullmatch(r"[0-9a-f]{24}", item_id):
            raise HTTPException(404, "图片不存在")
        for extension in ("png", "jpg", "webp"):
            path = image_dir / f"{item_id}.{extension}"
            if path.is_file():
                return FileResponse(path, filename=f"astrogate-{item_id}.{extension}", content_disposition_type="inline", headers={"Cache-Control": "private, max-age=86400"})
        raise HTTPException(404, "图片不存在")

    @app.delete("/api/images/history/{item_id}")
    def delete_history(item_id: str, user=Depends(current_user)):
        with database_connection(database_path) as db:
            count = db.execute("DELETE FROM image_history WHERE id = ? AND user_id = ?", (item_id, user["id"])).rowcount
        if not count:
            raise HTTPException(404, "记录不存在")
        if re.fullmatch(r"[0-9a-f]{24}", item_id):
            for extension in ("png", "jpg", "webp"):
                (image_dir / f"{item_id}.{extension}").unlink(missing_ok=True)
        return {"ok": True}

    def reserve(user_id: int, key: str, fingerprint: str):
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM generation_jobs WHERE user_id = ? AND request_key = ?", (user_id, key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(409, "请求编号已用于其他内容")
                if previous["status"] == "succeeded":
                    item = db.execute("SELECT * FROM image_history WHERE id = ? AND user_id = ?", (previous["id"], user_id)).fetchone()
                    if item:
                        return previous["id"], dict(item)
                raise HTTPException(409, "该请求已提交，请查看图集或重新发起生成")
            if not db.execute("UPDATE users SET credits = credits - 1 WHERE id = ? AND credits >= 1", (user_id,)).rowcount:
                raise HTTPException(402, "积分不足，请先兑换充值；每张图片需要 1 积分")
            job_id = secrets.token_hex(12)
            db.execute("INSERT INTO generation_jobs (id, user_id, request_key, fingerprint, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)", (job_id, user_id, key, fingerprint, now_iso()))
            ledger_entry(db, user_id, -1, "generation", "生成图片 · 1 张", f"generation:{job_id}")
            return job_id, None

    def refund(job_id: str, message: str):
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT user_id, status FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if job and job["status"] == "pending":
                db.execute("UPDATE users SET credits = credits + 1 WHERE id = ?", (job["user_id"],))
                ledger_entry(db, job["user_id"], 1, "refund", "生成失败，积分已退回", f"refund:{job_id}")
                db.execute("UPDATE generation_jobs SET status = 'failed', error_message = ? WHERE id = ?", (message[:500], job_id))

    async def run_generation(body, references, user, request_key):
        if not config.upstream_url or not config.upstream_api_key:
            raise HTTPException(503, "图片服务暂未配置")
        if len(references) > MAX_REFERENCES:
            raise HTTPException(400, "最多可使用 8 张参考图")
        decoded = [decode_reference(value) for value in references]
        if sum(size for _, size in decoded) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "参考图总大小不能超过 50MB")
        mode = "image-to-image" if references else "text-to-image"
        payload = {"prompt": body.prompt, "model": body.model, "size": body.size, "quality": body.quality, "n": 1, "response_format": "b64_json", "stream": False}
        if references:
            payload["images"] = [item for item, _ in decoded]
        key = request_key or secrets.token_hex(16)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", key):
            raise HTTPException(400, "请求编号无效")
        fingerprint = hash_token(json.dumps(payload, sort_keys=True))
        job_id, previous = reserve(user["id"], key, fingerprint)
        if previous:
            return {"item": previous, "credits": current_balance(user["id"])}
        output_path = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=15), transport=config.upstream_transport, trust_env=False) as client:
                result = await client.post(f"{config.upstream_url}/v1/images/{'edits' if references else 'generations'}", headers={"Authorization": f"Bearer {config.upstream_api_key}"}, json=payload)
                if result.is_error:
                    try:
                        message = image_error_detail(result.json())
                    except ValueError:
                        message = f"图片服务返回 HTTP {result.status_code}"
                    raise HTTPException(429 if result.status_code == 429 else 502, message)
                data = result.json()
                items = data.get("data") if isinstance(data, dict) else None
                first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
                if first.get("b64_json"):
                    output = base64.b64decode(first["b64_json"], validate=True)
                elif str(first.get("url", "")).startswith(("https://", "http://")):
                    chunks = bytearray()
                    async with client.stream("GET", first["url"]) as downloaded:
                        downloaded.raise_for_status()
                        async for chunk in downloaded.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > MAX_IMAGE_BYTES:
                                raise HTTPException(502, "生成图片超过大小限制")
                    output = bytes(chunks)
                else:
                    raise HTTPException(502, "图片服务未返回图片")
            if len(output) > MAX_IMAGE_BYTES:
                raise HTTPException(502, "生成图片超过大小限制")
            try:
                extension = image_extension(output)
            except HTTPException as exc:
                raise HTTPException(502, "图片服务返回了无效图片") from exc
            output_path = image_dir / f"{job_id}.{extension}"
            output_path.write_bytes(output)
            item = {"id": job_id, "prompt": body.prompt, "model": body.model, "size": body.size, "quality": body.quality,
                    "mode": mode, "reference_count": len(references), "credits_cost": 1, "image_src": f"/api/images/files/{job_id}", "status": "succeeded", "created_at": now_iso()}
            with database_connection(database_path) as db:
                db.execute("INSERT INTO image_history (id, user_id, prompt, model, size, quality, mode, reference_count, credits_cost, image_src, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (job_id, user["id"], body.prompt, body.model, body.size, body.quality, mode, len(references), 1, item["image_src"], "succeeded", item["created_at"]))
                db.execute("UPDATE generation_jobs SET status = 'succeeded' WHERE id = ?", (job_id,))
            return {"item": item, "credits": current_balance(user["id"])}
        except BaseException as exc:
            refund(job_id, str(exc))
            if output_path:
                output_path.unlink(missing_ok=True)
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, (httpx.HTTPError, ValueError, binascii.Error)):
                raise HTTPException(502, "图片生成未完成，积分已退回，请稍后再试") from exc
            raise

    def current_balance(user_id):
        with database_connection(database_path) as db:
            return db.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()[0]

    @app.post("/api/images/generations")
    async def generate_image(body: ImageGenerationRequest, user=Depends(current_user), idempotency_key: Annotated[str | None, Header()] = None):
        return await run_generation(body, [], user, idempotency_key)

    @app.post("/api/images/edits")
    async def edit_image(body: ImageEditRequest, user=Depends(current_user), idempotency_key: Annotated[str | None, Header()] = None):
        references = list(body.image_data_urls)
        if body.image_data_url:
            references.append(body.image_data_url)
        if not references:
            raise HTTPException(400, "请先添加参考图")
        return await run_generation(body, references, user, idempotency_key)

    return app


app = create_app()
