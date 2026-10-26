from __future__ import annotations

import base64
import binascii
from io import BytesIO
from decimal import Decimal, InvalidOperation
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from threading import Lock
import time
from typing import Annotated, Any, Callable, Literal
from urllib.parse import parse_qsl, urlparse

import httpx
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator
from PIL import Image, ImageOps, UnidentifiedImageError

from app.mail import send_registration_code
from app.community_media import MAX_POST_BYTES, MAX_POST_IMAGES, MAX_UPLOAD_BYTES, store_post_images
from app.upstream_admin import project_accounts
from app.gateway_bridge import create_gateway_router

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "app" / "static"
SESSION_COOKIE = "astrogate_session"
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_REFERENCES = 8
RESOLUTION_EDGES = {"1K": 1024, "2K": 2048, "3K": 3072, "4K": 3840}
PASSWORD_ITERATIONS = 600_000
IMAGE_MODELS = (
    {"id": "gpt-image-2", "label": "GPT Image 2.5 · 标准", "credits_cost": 1, "price_rmb": "0.05"},
    {"id": "gpt-image-2.5-flare", "label": "GPT Image 2.5 Flare", "credits_cost": 5, "price_rmb": "0.25"},
    {"id": "gpt-image-2.5-sunburst", "label": "GPT Image 2.5 Sunburst", "credits_cost": 5, "price_rmb": "0.25"},
)
IMAGE_MODEL_BY_ID = {item["id"]: item for item in IMAGE_MODELS}
PROMPT_TAGS = {
    "scene": ("云海古城", "樱花街道", "雨夜都市", "月光森林", "海边日落", "雪山晨曦", "星际港口", "水下宫殿", "沙漠绿洲", "空中花园", "未来地铁", "山间茶屋", "极光冰原", "雾中灯塔", "古风庭院", "森林小屋", "云端神殿", "海底遗迹", "雨后花园", "赛博街区"),
    "character": ("银发魔法师", "都市旅人", "花间少女", "机甲骑士", "猫耳店员", "森林精灵", "宇航员", "古风剑客", "赛博侦探", "海洋守护者", "龙族少年", "舞台歌手", "蒸汽工匠", "白袍学者", "未来赛车手", "童话公主", "夜行侠客", "机械人偶", "雪国旅者", "咖啡馆主"),
    "style": ("电影感摄影", "精致动漫", "水彩绘本", "油画笔触", "黑白漫画", "黏土定格", "像素艺术", "复古海报", "极简设计", "梦幻插画", "赛博朋克", "蒸汽波", "国风工笔", "低多边形", "纸雕艺术", "胶片摄影", "浮世绘", "3D 渲染", "铅笔素描", "童话绘本"),
    "commercial": ("香水广告", "珠宝特写", "咖啡包装", "甜品陈列", "家具大片", "运动鞋海报", "护肤品静物", "潮玩展示", "汽车宣传", "时尚杂志", "花艺品牌", "耳机产品", "手表广告", "书店橱窗", "茶饮海报", "酒店空间", "家居用品", "美妆主视觉", "礼盒设计", "建筑概念"),
    "science": ("纳米药物", "细胞成像", "蛋白结构", "宇宙星云", "气候地图", "植物解剖", "深海生态", "量子芯片", "人体骨骼", "太阳能电池", "脑神经网络", "材料晶格", "基因图谱", "显微世界", "火山剖面", "海洋洋流", "机械传动", "分子模型", "地质年代", "生物发光"),
}
PRICE = {"credits_per_image": 1, "rmb_per_image": "0.05", "rmb_per_credit": "0.05",
         "models": {item["id"]: {"credits_cost": item["credits_cost"], "price_rmb": item["price_rmb"]} for item in IMAGE_MODELS}}
PAYMENT_TIERS = {1: 20, 5: 120, 10: 300}
GALLERY_POSTS = json.loads((ROOT_DIR / "app/gallery_catalog.json").read_text(encoding="utf-8"))
GALLERY_BY_ID = {post["id"]: post for post in GALLERY_POSTS}


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
    text_api_base: str = ""
    text_api_key: str = ""
    text_model: str = "gpt-5.6-luna"
    text_transport: httpx.AsyncBaseTransport | None = None
    gateway_bridge_key: str = ""
    gateway_public_origin: str = "https://img.astrogate.sbs"
    payment_api_base: str = "https://pay.1kexiu.xyz"
    payment_pid: str = ""
    payment_key: str = ""

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
            text_api_base=os.getenv("ASTROGATE_TEXT_API_BASE", "").strip().rstrip("/"),
            text_api_key=os.getenv("ASTROGATE_TEXT_API_KEY", "").strip(),
            text_model=os.getenv("ASTROGATE_TEXT_MODEL", "gpt-5.6-luna").strip(),
            gateway_bridge_key=os.getenv("ASTROGATE_GATEWAY_BRIDGE_KEY", "").strip(),
            gateway_public_origin=os.getenv("ASTROGATE_GATEWAY_PUBLIC_ORIGIN", "https://img.astrogate.sbs").strip(),
            payment_api_base=os.getenv("ASTROGATE_PAYMENT_API_BASE", "https://pay.1kexiu.xyz").strip().rstrip("/"),
            payment_pid=os.getenv("ASTROGATE_PAYMENT_PID", "").strip(),
            payment_key=os.getenv("ASTROGATE_PAYMENT_KEY", "").strip(),
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
    model: Literal["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"] = "gpt-image-2"
    size: Literal[
        "1024x1024", "1024x1536", "1536x1024",
        "1280x960", "960x1280", "1536x864", "864x1536",
        "1024x1280", "1280x1024", "1536x768", "768x1536",
        "1792x768", "768x1792",
    ] = "1536x864"
    quality: str = Field(default="medium", pattern=r"^(low|medium|high)$")
    resolution: Literal["original", "1K", "2K", "3K", "4K"] = "original"

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写画面描述")
        return value.strip()


class ImageEditRequest(ImageGenerationRequest):
    image_data_urls: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    image_data_url: str | None = None  # Compatibility with the first client release.


class PromptAssistRequest(BaseModel):
    idea: str = Field(min_length=2, max_length=1200)

    @field_validator("idea")
    @classmethod
    def trim_idea(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("请先写一点画面想法")
        return value


class PromptTagsRequest(BaseModel):
    category: Literal["scene", "character", "style", "commercial", "science"]
    exclude: list[str] = Field(default_factory=list, max_length=20)


class CommunityLikeRequest(BaseModel):
    liked: bool


class CommunityPostRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(default="", max_length=4000)
    prompt: str = Field(default="", max_length=4000)
    category: Literal["illustration", "collage", "science", "photography", "design", "general"] = "general"
    tags: list[Annotated[str, Field(min_length=1, max_length=20)]] = Field(default_factory=list, max_length=5)
    image_data_urls: list[Annotated[str, Field(max_length=14_000_000)]] = Field(default_factory=list, max_length=MAX_POST_IMAGES)
    source_history_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{24}$")

    @field_validator("title", "content", "prompt")
    @classmethod
    def trim_text(cls, value: str, info) -> str:
        value = value.strip()
        if info.field_name == "title" and not value:
            raise ValueError("请填写帖子标题")
        return value


class UpstreamRefreshRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=500)


class CommunityCommentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=600)

    @field_validator("content")
    @classmethod
    def nonempty_comment(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写评论内容")
        return value.strip()


class RedeemRequest(BaseModel):
    code: str = Field(min_length=8, max_length=120)


class PaymentOrderRequest(BaseModel):
    tier: Literal[1, 5, 10]


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


def easy_pay_sign(fields: dict[str, str], key: str) -> str:
    source = "&".join(f"{name}={value}" for name, value in sorted(fields.items())
                      if name not in {"sign", "sign_type"} and value)
    return hashlib.md5((source + key).encode("utf-8")).hexdigest()


def payment_amount_cents(value: str) -> int | None:
    try:
        amount = Decimal(value)
        cents = amount * 100
        return int(cents) if amount.is_finite() and cents == cents.to_integral_value() and cents > 0 else None
    except (InvalidOperation, ValueError):
        return None


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
                credits_cost INTEGER NOT NULL DEFAULT 0, resolution TEXT NOT NULL DEFAULT 'original', output_size TEXT,
                source_size TEXT
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
            CREATE TABLE IF NOT EXISTS payment_orders (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                amount_cents INTEGER NOT NULL CHECK(amount_cents IN (100, 500, 1000)),
                credits INTEGER NOT NULL CHECK(credits > 0),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'paid')),
                platform_trade_no TEXT UNIQUE, created_at TEXT NOT NULL, paid_at TEXT
            );
            CREATE INDEX IF NOT EXISTS payment_orders_by_user ON payment_orders(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS generation_jobs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, error_message TEXT,
                credits_cost INTEGER NOT NULL DEFAULT 1, prompt TEXT,
                UNIQUE(user_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS email_verifications (
                email TEXT PRIMARY KEY COLLATE NOCASE, code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL, requested_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS email_verification_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, requested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS community_likes (
                post_id TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL, PRIMARY KEY(post_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS community_posts (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL, content TEXT NOT NULL, prompt TEXT NOT NULL,
                category TEXT NOT NULL, tags_json TEXT NOT NULL, images_json TEXT NOT NULL,
                size TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS community_posts_by_user ON community_posts(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS community_posts_by_date ON community_posts(created_at DESC);
            CREATE TABLE IF NOT EXISTS community_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 600), created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS community_comments_by_post ON community_comments(post_id, id DESC);
            CREATE INDEX IF NOT EXISTS verification_sends_by_email ON email_verification_sends(email, requested_at);
            CREATE INDEX IF NOT EXISTS image_history_by_user ON image_history(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS ledger_by_user ON credit_ledger(user_id, id DESC);
        """)
        migrations = {
            "users": {"credits": "INTEGER NOT NULL DEFAULT 0", "is_admin": "INTEGER NOT NULL DEFAULT 0", "email": "TEXT COLLATE NOCASE", "email_verified_at": "TEXT"},
            "image_history": {"mode": "TEXT NOT NULL DEFAULT 'text-to-image'", "reference_count": "INTEGER NOT NULL DEFAULT 0", "credits_cost": "INTEGER NOT NULL DEFAULT 0", "resolution": "TEXT NOT NULL DEFAULT 'original'", "output_size": "TEXT", "source_size": "TEXT"},
            "generation_jobs": {"credits_cost": "INTEGER NOT NULL DEFAULT 1", "prompt": "TEXT"},
        }
        for table, fields in migrations.items():
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in fields.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_by_email ON users(email COLLATE NOCASE) WHERE email IS NOT NULL")
        db.execute("UPDATE generation_jobs SET prompt = (SELECT prompt FROM image_history WHERE image_history.id = generation_jobs.id) WHERE prompt IS NULL AND status = 'succeeded' AND EXISTS (SELECT 1 FROM image_history WHERE image_history.id = generation_jobs.id)")
        if settings.admin_username and settings.admin_password:
            Credentials(username=settings.admin_username, password=settings.admin_password)
            admin = db.execute("SELECT id FROM users WHERE username = ?", (settings.admin_username,)).fetchone()
            if admin:
                db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin[0],))
            else:
                db.execute("INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?, ?, ?, 1)",
                           (settings.admin_username, hash_password(settings.admin_password), now_iso()))
        # One application worker owns jobs. Refund reservations interrupted by a restart.
        for job in db.execute("SELECT id, user_id, credits_cost FROM generation_jobs WHERE status = 'pending'").fetchall():
            cost = job["credits_cost"]
            db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (cost, job["user_id"]))
            ledger_entry(db, job["user_id"], cost, "refund", "服务重启，积分已退回", f"refund:{job['id']}")
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


def generation_failure_reason(message: str | None) -> str:
    if not message:
        return "未记录原因"
    if any(word in message for word in ("裸露", "色情", "情色")):
        return "上游内容限制：裸露或情色"
    if "生成的图片可能违反" in message:
        return "生成结果被上游内容审核拦截"
    if "内容政策" in message:
        return "提示词被上游内容审核拦截"
    if "超时" in message:
        return "上游生成超时"
    if "重启" in message:
        return "服务重启，积分已退回"
    if "限流" in message or "429" in message:
        return "上游账号限流"
    if "额度" in message or "quota" in message.lower():
        return "上游图片额度不足"
    return "上游生成失败，积分已退回"


def admin_job_record(row) -> dict[str, Any]:
    return {"id": row["id"], "user_id": row["user_id"], "username": row["username"],
            "email": row["email"], "status": row["status"], "created_at": row["created_at"],
            "credits_cost": row["credits_cost"], "prompt": row["prompt"] or row["saved_prompt"],
            "image_url": f"/api/admin/images/files/{row['id']}" if row["status"] == "succeeded" and row["image_id"] else None,
            "failure_reason": generation_failure_reason(row["error_message"]) if row["status"] == "failed" else None}


def image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise HTTPException(400, "图片内容无效，仅支持 PNG、JPG、WebP")


def export_dimensions(size: str, resolution: str) -> tuple[int, int] | None:
    if resolution == "original":
        return None
    width, height = (int(part) for part in size.split("x"))
    edge = RESOLUTION_EDGES[resolution]
    if width >= height:
        return edge, max(2, round(edge * height / width / 2) * 2)
    return max(2, round(edge * width / height / 2) * 2), edge


def prepare_generated_image(data: bytes, target: tuple[int, int] | None) -> tuple[bytes, str, str, str]:
    extension = image_extension(data)
    try:
        with Image.open(BytesIO(data)) as opened:
            source_size = f"{opened.width}x{opened.height}"
            if target is None:
                return data, extension, source_size, source_size
            image = ImageOps.exif_transpose(opened)
            image.load()
            image = image.convert("RGBA" if image.mode in ("RGBA", "LA") or "transparency" in image.info else "RGB")
            if image.size != target:
                image = ImageOps.fit(image, target, method=Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="PNG", compress_level=4)
            output = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(502, "图片服务返回了无效图片") from exc
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(502, "生成图片超过大小限制")
    return output, "png", f"{target[0]}x{target[1]}", source_size


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
    model_cache: dict[str, Any] = {"expires": 0.0, "ids": set()}
    model_cache_lock = Lock()

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

    app = FastAPI(title="AstroGate IMG", version="0.6.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(create_gateway_router(config))
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/community/posts":
            try:
                if int(request.headers.get("content-length", "0")) > 43 * 1024 * 1024:
                    return JSONResponse({"detail": "图片合计不能超过 30MB"}, 413)
            except ValueError:
                return JSONResponse({"detail": "请求长度无效"}, 400)
        origin = request.headers.get("origin")
        if request.method in {"POST", "DELETE", "PUT", "PATCH"} and origin:
            if urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请求来源无效"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith(("/api/", "/internal/")):
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
                retry_after = max(1, math.ceil(queue[0] + period - now))
                raise HTTPException(429, "操作过于频繁，请稍后再试", headers={"Retry-After": str(retry_after)})
            queue.append(now)

    def rate_limit_user(user_id: int, kind: str, limit: int, period: int) -> None:
        key = f"{kind}:user:{user_id}"
        with attempt_lock:
            queue = attempts[key]
            now = time.monotonic()
            while queue and queue[0] < now - period:
                queue.popleft()
            if len(queue) >= limit:
                retry_after = max(1, math.ceil(queue[0] + period - now))
                raise HTTPException(429, "操作过于频繁，请稍后再试", headers={"Retry-After": str(retry_after)})
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

    def optional_user(session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None):
        try:
            return current_user(session_token)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            return None

    def gallery_post(post_id: str, db):
        if post_id in GALLERY_BY_ID:
            return {**GALLERY_BY_ID[post_id], "is_curated": True, "content": "", "created_at": "", "user_id": None}
        row = db.execute("SELECT p.*, u.username, u.email FROM community_posts p JOIN users u ON u.id = p.user_id WHERE p.id = ?", (post_id,)).fetchone()
        if not row:
            raise HTTPException(404, "作品不存在")
        images = json.loads(row["images_json"])
        return {**dict(row), "author": row["email"].split("@")[0] if row["email"] else row["username"],
                "tags": json.loads(row["tags_json"]), "images": images, "is_curated": False, "comments": [],
                "alt": row["title"], "width": images[0]["width"] if images else 0, "height": images[0]["height"] if images else 0}

    def community_comment(row, user):
        return {"id": row["id"], "author": row["email"].split("@")[0] if row["email"] else row["username"],
                "content": row["content"], "created_at": row["created_at"], "is_example": False,
                "can_delete": bool(user and (user["id"] == row["user_id"] or user["is_admin"]))}

    def example_comments(post):
        return [{"id": f"example-{post['id']}-{index}", "author": comment["author"], "content": comment["content"],
                 "is_example": True, "can_delete": False, "created_at": None} for index, comment in enumerate(post["comments"])]

    def community_post(post, db, user):
        item = {key: post[key] for key in ("id", "title", "category", "tags", "author", "prompt", "alt", "width", "height", "size", "content", "created_at", "is_curated")}
        item["images"] = [{"image": f"/assets/gallery/{post['id']}.webp", "thumbnail": f"/assets/gallery/{post['id']}-preview.webp", "width": post["width"], "height": post["height"]}] if post["is_curated"] else post["images"]
        item["image"] = item["images"][0]["image"] if item["images"] else ""
        item["thumbnail"] = item["images"][0]["thumbnail"] if item["images"] else ""
        item["is_mine"] = bool(user and post["user_id"] == user["id"])
        item["can_delete"] = bool(not post["is_curated"] and user and (item["is_mine"] or user["is_admin"]))
        item["example_likes_count"] = post.get("example_likes", 0) if post["is_curated"] else 0
        item["real_likes_count"] = db.execute("SELECT COUNT(*) FROM community_likes WHERE post_id = ?", (post["id"],)).fetchone()[0]
        item["likes_count"] = item["real_likes_count"] + item["example_likes_count"]
        item["liked"] = bool(user and db.execute("SELECT 1 FROM community_likes WHERE post_id = ? AND user_id = ?", (post["id"], user["id"])).fetchone())
        item["comments_count"] = len(post["comments"]) + db.execute("SELECT COUNT(*) FROM community_comments WHERE post_id = ?", (post["id"],)).fetchone()[0]
        latest = db.execute("SELECT c.*, u.username, u.email FROM community_comments c JOIN users u ON u.id = c.user_id WHERE c.post_id = ? ORDER BY c.id DESC LIMIT 1", (post["id"],)).fetchone()
        examples = example_comments(post)
        item["comment_preview"] = community_comment(latest, user) if latest else (examples[0] if examples else None)
        item["last_commented_at"] = latest["created_at"] if latest else ""
        return item

    @app.get("/api/community/posts")
    def list_community_posts(page: int = Query(default=0, ge=0, le=10000), mine: bool = False, user=Depends(optional_user)):
        if mine and not user:
            raise HTTPException(401, "请先登录")
        with database_connection(database_path) as db:
            where, params = ("WHERE user_id = ?", [user["id"]]) if mine else ("", [])
            rows = db.execute(f"SELECT id FROM community_posts {where} ORDER BY created_at DESC, id DESC LIMIT 31 OFFSET ?", (*params, page * 30)).fetchall()
            items = [community_post(gallery_post(row["id"], db), db, user) for row in rows[:30]]
            if page == 0 and not mine:
                items.extend(community_post(gallery_post(post["id"], db), db, user) for post in GALLERY_POSTS)
        return {"items": items, "has_more": len(rows) > 30, "page": page,
                "user": {key: user[key] for key in ("id", "display_name", "is_admin")} if user else None}

    @app.post("/api/community/posts", status_code=201)
    def publish_community_post(body: CommunityPostRequest, user=Depends(current_user)):
        if body.source_history_id and body.image_data_urls:
            raise HTTPException(400, "请选择上传图片或作品库图片中的一种方式")
        images = []
        size = "1024x1024"
        with database_connection(database_path) as db:
            if db.execute("SELECT COUNT(*) FROM community_posts WHERE user_id = ? AND created_at > ?", (user["id"], (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())).fetchone()[0] >= 3:
                raise HTTPException(429, "发布太快了，请稍后再试", headers={"Retry-After": "60"})
            if body.source_history_id:
                source = db.execute("SELECT * FROM image_history WHERE id = ? AND user_id = ? AND status = 'succeeded'", (body.source_history_id, user["id"])).fetchone()
                if not source:
                    raise HTTPException(404, "作品不存在或不属于当前账号")
                for extension in ("png", "jpg", "webp"):
                    path = image_dir / f"{body.source_history_id}.{extension}"
                    if path.is_file():
                        images.append(path.read_bytes())
                        break
                if not images:
                    raise HTTPException(404, "作品图片不存在")
                size = source["size"]
        total = 0
        for value in body.image_data_urls:
            header, separator, encoded = value.partition(",")
            if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
                raise HTTPException(400, "仅支持 PNG、JPG 或 WebP 图片")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise HTTPException(400, "图片编码无效") from exc
            total += len(data)
            if len(data) > MAX_UPLOAD_BYTES or total > MAX_POST_BYTES:
                raise HTTPException(400, "单张不超过 10MB，合计不超过 30MB")
            images.append(data)
        if not images and not body.content:
            raise HTTPException(400, "请添加图片或写一点帖子正文")
        post_id = "post-" + secrets.token_hex(12)
        directory = config.data_dir / "community" / post_id
        try:
            media = store_post_images(images, directory, post_id) if images else []
            with database_connection(database_path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM community_posts WHERE user_id = ? AND created_at > ?", (user["id"], (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())).fetchone()[0] >= 3:
                    raise HTTPException(429, "发布太快了，请稍后再试", headers={"Retry-After": "60"})
                db.execute("INSERT INTO community_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (post_id, user["id"], body.title, body.content, body.prompt, body.category,
                            json.dumps(list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip())), ensure_ascii=False), json.dumps(media), size, now_iso()))
                item = community_post(gallery_post(post_id, db), db, user)
        except BaseException:
            if directory.is_dir():
                shutil.rmtree(directory)
            raise
        return {"item": item}

    @app.get("/community-media/{post_id}/{filename}")
    def community_media(post_id: str, filename: str):
        if not re.fullmatch(r"post-[0-9a-f]{24}", post_id) or not re.fullmatch(r"[0-8](?:-preview)?\.webp", filename):
            raise HTTPException(404, "图片不存在")
        with database_connection(database_path) as db:
            if not db.execute("SELECT 1 FROM community_posts WHERE id = ?", (post_id,)).fetchone():
                raise HTTPException(404, "图片不存在")
        path = config.data_dir / "community" / post_id / filename
        if not path.is_file():
            raise HTTPException(404, "图片不存在")
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-cache"})

    @app.delete("/api/community/posts/{post_id}")
    def delete_community_post(post_id: str, user=Depends(current_user)):
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            post = gallery_post(post_id, db)
            if post["is_curated"] or (post["user_id"] != user["id"] and not user["is_admin"]):
                raise HTTPException(403, "只能删除自己的帖子")
            db.execute("DELETE FROM community_comments WHERE post_id = ?", (post_id,))
            db.execute("DELETE FROM community_likes WHERE post_id = ?", (post_id,))
            db.execute("DELETE FROM community_posts WHERE id = ?", (post_id,))
        if re.fullmatch(r"post-[0-9a-f]{24}", post_id):
            directory = config.data_dir / "community" / post_id
            if directory.is_dir():
                shutil.rmtree(directory)
        return {"ok": True}

    @app.get("/api/community/posts/{post_id}")
    def get_community_post(post_id: str, user=Depends(optional_user)):
        with database_connection(database_path) as db:
            post = gallery_post(post_id, db)
            item = community_post(post, db, user)
            comments = db.execute("SELECT c.*, u.username, u.email FROM community_comments c JOIN users u ON u.id = c.user_id WHERE c.post_id = ? ORDER BY c.id DESC LIMIT 100", (post_id,)).fetchall()
        return {"item": item, "comments": example_comments(post) + [community_comment(row, user) for row in reversed(comments)]}

    @app.post("/api/community/posts/{post_id}/like")
    def set_community_like(post_id: str, body: CommunityLikeRequest, user=Depends(current_user)):
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            post = gallery_post(post_id, db)
            if body.liked:
                db.execute("INSERT OR IGNORE INTO community_likes VALUES (?, ?, ?)", (post_id, user["id"], now_iso()))
            else:
                db.execute("DELETE FROM community_likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
            count = db.execute("SELECT COUNT(*) FROM community_likes WHERE post_id = ?", (post_id,)).fetchone()[0]
        return {"liked": body.liked, "real_likes_count": count, "likes_count": count + post.get("example_likes", 0)}

    @app.post("/api/community/posts/{post_id}/comments", status_code=201)
    def add_community_comment(post_id: str, body: CommunityCommentRequest, request: Request, user=Depends(current_user)):
        rate_limit(request, "community-comment", 10, 60)
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            post = gallery_post(post_id, db)
            cursor = db.execute("INSERT INTO community_comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)", (post_id, user["id"], body.content, now_iso()))
            row = db.execute("SELECT c.*, u.username, u.email FROM community_comments c JOIN users u ON u.id = c.user_id WHERE c.id = ?", (cursor.lastrowid,)).fetchone()
            count = len(post["comments"]) + db.execute("SELECT COUNT(*) FROM community_comments WHERE post_id = ?", (post_id,)).fetchone()[0]
        return {"comment": community_comment(row, user), "comments_count": count}

    @app.delete("/api/community/comments/{comment_id}")
    def delete_community_comment(comment_id: int, user=Depends(current_user)):
        with database_connection(database_path) as db:
            row = db.execute("SELECT * FROM community_comments WHERE id = ?", (comment_id,)).fetchone()
            if not row:
                raise HTTPException(404, "评论不存在")
            if row["user_id"] != user["id"] and not user["is_admin"]:
                raise HTTPException(403, "只能删除自己的评论")
            db.execute("DELETE FROM community_comments WHERE id = ?", (comment_id,))
        return {"ok": True}

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
    def studio():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/admin")
    @app.get("/admin/login")
    @app.get("/admin/accounts")
    @app.get("/admin/users")
    @app.get("/admin/jobs")
    @app.get("/admin/codes")
    def admin_page():
        return FileResponse(STATIC_DIR / "admin.html", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return {"ok": True, "upstream_configured": bool(config.upstream_url and config.upstream_api_key)}

    @app.get("/api/config")
    def public_config():
        return {"upstream_configured": bool(config.upstream_url and config.upstream_api_key), "pricing": PRICE,
                "email_registration": True, "max_reference_images": MAX_REFERENCES, "max_reference_bytes": MAX_IMAGE_BYTES}

    async def image_model_items() -> list[dict[str, Any]]:
        configured = bool(config.upstream_url and config.upstream_api_key)
        ids: set[str] = set()
        if configured:
            with model_cache_lock:
                fresh = time.monotonic() < model_cache["expires"]
                if fresh:
                    ids = set(model_cache["ids"])
            if not fresh:
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), transport=config.upstream_transport, trust_env=False) as client:
                        response = await client.get(f"{config.upstream_url}/v1/models", headers={"Authorization": f"Bearer {config.upstream_api_key}"})
                        response.raise_for_status()
                        payload = response.json()
                    data = payload.get("data", []) if isinstance(payload, dict) else []
                    ids = {item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}
                except (httpx.HTTPError, ValueError, TypeError):
                    ids = set()
                with model_cache_lock:
                    model_cache["ids"] = ids
                    model_cache["expires"] = time.monotonic() + 30
        return [{**item, "available": configured and (item["id"] == "gpt-image-2" or item["id"] in ids)}
                for item in IMAGE_MODELS]

    @app.get("/api/images/models")
    async def available_image_models():
        return {"items": await image_model_items()}

    text_service_state = {"retry_at": 0.0}

    async def complete_text(messages: list[dict[str, str]]) -> str | None:
        if not config.text_api_base or not config.text_api_key or not config.text_model or time.monotonic() < text_service_state["retry_at"]:
            return None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), transport=config.text_transport, trust_env=False) as client:
                response = await client.post(
                    f"{config.text_api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {config.text_api_key}"},
                    json={"model": config.text_model, "stream": False, "messages": messages},
                )
                response.raise_for_status()
                data = response.json()
            choices = data.get("choices", []) if isinstance(data, dict) else []
            content = choices[0].get("message", {}).get("content", "") if choices and isinstance(choices[0], dict) else ""
            if isinstance(content, str) and content.strip():
                text_service_state["retry_at"] = 0.0
                return content.strip()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            pass
        text_service_state["retry_at"] = time.monotonic() + 90
        return None

    @app.post("/api/prompts/assist")
    async def assist_prompt(body: PromptAssistRequest, user=Depends(current_user)):
        rate_limit_user(user["id"], "prompt-assist", 30, 600)
        prompt = await complete_text([
            {"role": "system", "content": "你是图片创作提示词助手。根据用户想法写一段可直接用于图像生成的中文提示词，明确主体、场景、构图、光线、色彩、氛围和细节；尊重原意，不虚构画面文字，不写模型参数。只返回提示词正文，控制在 100 至 500 个汉字。"},
            {"role": "user", "content": body.idea},
        ])
        if prompt:
            return {"prompt": prompt[:4000], "source": "ai"}
        idea = body.idea.strip().rstrip("，。；; ")
        fallback = f"{idea}。主体清晰，画面重点突出；安排前景、中景与背景，构图自然完整，透视关系准确。使用与主题相符的光线和配色，呈现材质细节、空间层次与氛围。"
        return {"prompt": fallback[:4000], "source": "fallback"}

    @app.post("/api/prompts/tags")
    async def prompt_tags(body: PromptTagsRequest, user=Depends(current_user)):
        rate_limit_user(user["id"], "prompt-tags", 20, 600)
        category_names = {"scene": "场景", "character": "人物", "style": "风格", "commercial": "商业", "science": "科研"}
        generated = await complete_text([
            {"role": "system", "content": "你为图像创作提供灵感标签。仅返回 JSON 字符串数组，恰好 12 个简短中文标签，每个 2 至 12 字，具体、多样、不重复。不附加解释。"},
            {"role": "user", "content": f"类别：{category_names[body.category]}。避开这些标签：{', '.join(body.exclude[:12])}。请给一组新标签。"},
        ])
        if generated:
            try:
                raw = generated.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                tags = json.loads(raw)
                if isinstance(tags, list):
                    clean = list(dict.fromkeys(tag.strip() for tag in tags if isinstance(tag, str) and 2 <= len(tag.strip()) <= 12 and tag.strip() not in body.exclude))
                    if len(clean) >= 10:
                        return {"tags": clean[:12], "source": "ai"}
            except (ValueError, TypeError):
                pass
        pool = list(PROMPT_TAGS[body.category])
        fresh = [tag for tag in pool if tag not in body.exclude]
        rng = secrets.SystemRandom()
        tags = rng.sample(fresh, min(12, len(fresh)))
        if len(tags) < 12:
            tags.extend(rng.sample([tag for tag in pool if tag not in tags], 12 - len(tags)))
        rng.shuffle(tags)
        return {"tags": tags, "source": "fallback"}

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
            resend_after = max(0, math.ceil(60 - (now - datetime.fromisoformat(existing["requested_at"])).total_seconds())) if existing else 0
            if resend_after:
                raise HTTPException(429, f"请 {resend_after} 秒后重新获取验证码", headers={"Retry-After": str(resend_after)})
            cutoff = (now - timedelta(hours=1)).isoformat()
            recent = db.execute("SELECT COUNT(*) AS total, MIN(requested_at) AS first_sent_at FROM email_verification_sends WHERE email = ? AND requested_at >= ?", (body.email, cutoff)).fetchone()
            if recent["total"] >= 5:
                retry_after = max(1, math.ceil(3600 - (now - datetime.fromisoformat(recent["first_sent_at"])).total_seconds()))
                raise HTTPException(429, "该邮箱发送次数过多，请稍后再试", headers={"Retry-After": str(retry_after)})
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
            retry_after = max(0, math.ceil(60 - (datetime.now(timezone.utc) - now).total_seconds()))
            raise HTTPException(502, "邮件暂时未能发送，请稍后重试；若已收到邮件，可继续使用验证码", headers={"Retry-After": str(retry_after)}) from exc
        elapsed = (datetime.now(timezone.utc) - now).total_seconds()
        return {"ok": True, "expires_in": max(0, math.ceil(600 - elapsed)), "resend_after": max(0, math.ceil(60 - elapsed))}

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

    @app.post("/api/admin/login")
    def admin_login(body: Credentials, response: Response, request: Request):
        rate_limit(request, "admin-login", 10, 60)
        with database_connection(database_path) as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
        if not row or not row["is_admin"] or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "管理员账号或密码错误")
        issue_session(response, row["id"])
        return {"user": public_user(row)}

    @app.get("/api/admin/me")
    def admin_me(user=Depends(admin_user)):
        return {"user": user}

    async def upstream_admin_request(method, path, body=None):
        if not config.upstream_url or not config.upstream_api_key:
            raise HTTPException(503, "上游管理连接尚未配置")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=10), transport=config.upstream_transport, trust_env=False) as client:
                response = await client.request(method, config.upstream_url + path, headers={"Authorization": f"Bearer {config.upstream_api_key}"}, json=body)
                if response.is_error:
                    raise HTTPException(502, f"上游管理接口返回 HTTP {response.status_code}")
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("invalid response")
                return data
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "上游管理暂时无法连接，请稍后重试") from exc

    @app.get("/api/admin/upstream/accounts")
    async def upstream_accounts(user=Depends(admin_user)):
        result = await upstream_admin_request("GET", "/api/accounts")
        public, _ = project_accounts(result.get("items") or [])
        return {**public, "queried_at": now_iso()}

    @app.post("/api/admin/upstream/refresh")
    async def upstream_refresh(body: UpstreamRefreshRequest, request: Request, user=Depends(admin_user)):
        rate_limit(request, "admin-upstream-refresh", 6, 60)
        result = await upstream_admin_request("GET", "/api/accounts")
        _, token_groups = project_accounts(result.get("items") or [])
        ids = body.account_ids or list(token_groups)
        if not ids or any(key not in token_groups for key in ids):
            raise HTTPException(400, "请选择有效的上游账号")
        tokens = list(dict.fromkeys(token for key in ids for token in token_groups[key]))
        progress = await upstream_admin_request("POST", "/api/accounts/refresh", {"access_tokens": tokens})
        progress_id = str(progress.get("progress_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", progress_id):
            raise HTTPException(502, "上游未返回刷新任务编号")
        return {"progress_id": progress_id}

    @app.get("/api/admin/upstream/refresh/{progress_id}")
    async def upstream_refresh_progress(progress_id: str, user=Depends(admin_user)):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", progress_id):
            raise HTTPException(400, "刷新任务编号无效")
        progress = await upstream_admin_request("GET", f"/api/accounts/refresh/progress/{progress_id}")
        return {key: progress.get(key) for key in ("total", "processed", "done", "status_counts", "total_quota")} | {
            "error_count": len((progress.get("result") or {}).get("errors") or []),
            "error": "上游刷新任务未完成，请重试" if progress.get("error") else None}

    @app.get("/api/admin/overview")
    def admin_overview(user=Depends(admin_user)):
        start = datetime.now(timezone(timedelta(hours=8))).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        with database_connection(database_path) as db:
            customers = dict(db.execute("SELECT COUNT(*) AS users, COALESCE(SUM(credits), 0) AS credits FROM users WHERE is_admin = 0").fetchone())
            jobs = dict(db.execute("SELECT COUNT(*) AS generated, COALESCE(SUM(created_at >= ?), 0) AS generated_today FROM generation_jobs WHERE status = 'succeeded'", (start,)).fetchone())
            pending = db.execute("SELECT COUNT(*) FROM generation_jobs WHERE status = 'pending'").fetchone()[0]
            codes = dict(db.execute("SELECT COUNT(*) AS total, COALESCE(SUM(redeemed_by IS NOT NULL), 0) AS redeemed FROM redemption_codes").fetchone())
            recent = db.execute("SELECT j.id, j.status, j.created_at, j.error_message, u.username FROM generation_jobs j JOIN users u ON u.id = j.user_id ORDER BY j.created_at DESC LIMIT 10").fetchall()
        recent_jobs = [{key: row[key] for key in ("id", "status", "created_at", "username")} |
                       {"failure_reason": generation_failure_reason(row["error_message"]) if row["status"] == "failed" else None}
                       for row in recent]
        return {"customers": customers, "jobs": {**jobs, "pending": pending}, "codes": codes, "recent_jobs": recent_jobs}

    @app.get("/api/wallet")
    def wallet(user=Depends(current_user)):
        with database_connection(database_path) as db:
            rows = db.execute("SELECT amount, balance_after, kind, description, created_at FROM credit_ledger WHERE user_id = ? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
            orders = db.execute("SELECT id, amount_cents, credits, status, created_at, paid_at FROM payment_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (user["id"],)).fetchall()
        return {"credits": user["credits"], "pricing": PRICE, "ledger": [dict(row) for row in rows],
                "payment_enabled": bool(config.payment_pid and config.payment_key),
                "payment_tiers": [{"yuan": yuan, "credits": credits} for yuan, credits in PAYMENT_TIERS.items()],
                "payment_orders": [dict(row) for row in orders]}

    @app.post("/api/payments/orders", status_code=201)
    def create_payment_order(body: PaymentOrderRequest, request: Request, user=Depends(current_user)):
        if not config.payment_pid or not config.payment_key:
            raise HTTPException(503, "支付宝充值暂未开放")
        payment_url = urlparse(config.payment_api_base)
        public_url = urlparse(config.gateway_public_origin)
        if payment_url.scheme != "https" or not payment_url.netloc or payment_url.username or payment_url.password or payment_url.query or payment_url.fragment or public_url.scheme != "https" or not public_url.netloc:
            raise HTTPException(503, "支付配置无效")
        rate_limit(request, "payment-order", 30, 3600)
        rate_limit_user(user["id"], "payment-order", 10, 3600)
        order_id = "AGI" + secrets.token_hex(12).upper()
        credits = PAYMENT_TIERS[body.tier]
        with database_connection(database_path) as db:
            db.execute("INSERT INTO payment_orders (id, user_id, amount_cents, credits, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                       (order_id, user["id"], body.tier * 100, credits, now_iso()))
        origin = config.gateway_public_origin.rstrip("/")
        fields = {"pid": config.payment_pid, "type": "alipay", "out_trade_no": order_id,
                  "notify_url": origin + "/api/payments/notify", "return_url": origin + "/wallet?order=" + order_id,
                  "name": f"AstroGate IMG {credits}积分", "money": f"{body.tier:.2f}", "sitename": "AstroGate IMG"}
        fields["sign"] = easy_pay_sign(fields, config.payment_key)
        fields["sign_type"] = "MD5"
        return {"id": order_id, "credits": credits, "amount_cents": body.tier * 100,
                "action_url": config.payment_api_base + "/submit.php", "fields": fields}

    @app.get("/api/payments/orders/{order_id}")
    def get_payment_order(order_id: str, user=Depends(current_user)):
        with database_connection(database_path) as db:
            row = db.execute("SELECT id, amount_cents, credits, status, created_at, paid_at FROM payment_orders WHERE id = ? AND user_id = ?", (order_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        return dict(row)

    @app.api_route("/api/payments/notify", methods=["GET", "POST"])
    async def payment_notify(request: Request):
        if not config.payment_pid or not config.payment_key:
            raise HTTPException(503, "支付配置无效")
        if request.method == "POST":
            raw = await request.body()
            if len(raw) > 4096:
                raise HTTPException(400, "回调数据无效")
            try:
                pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
            except (UnicodeError, ValueError) as exc:
                raise HTTPException(400, "回调数据无效") from exc
        else:
            pairs = list(request.query_params.multi_items())
        if len(pairs) > 30 or len({name for name, _ in pairs}) != len(pairs):
            raise HTTPException(400, "回调参数无效")
        fields = dict(pairs)
        signature = fields.get("sign", "")
        if (fields.get("sign_type", "").upper() != "MD5" or not re.fullmatch(r"[0-9a-fA-F]{32}", signature)
                or not hmac.compare_digest(easy_pay_sign(fields, config.payment_key), signature.lower())):
            raise HTTPException(400, "回调签名无效")
        if fields.get("pid") != config.payment_pid or fields.get("type") != "alipay" or fields.get("trade_status") != "TRADE_SUCCESS":
            raise HTTPException(400, "回调状态无效")
        order_id = fields.get("out_trade_no", "")
        trade_no = fields.get("trade_no", "")
        cents = payment_amount_cents(fields.get("money", ""))
        if not re.fullmatch(r"AGI[0-9A-F]{24}", order_id) or not 1 <= len(trade_no) <= 128 or cents is None:
            raise HTTPException(400, "回调订单无效")
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM payment_orders WHERE id = ?", (order_id,)).fetchone()
            if not row or row["amount_cents"] != cents or row["credits"] != PAYMENT_TIERS.get(cents // 100):
                raise HTTPException(400, "回调金额无效")
            if row["status"] == "paid":
                if row["platform_trade_no"] != trade_no:
                    raise HTTPException(400, "订单交易号不一致")
            else:
                if db.execute("SELECT 1 FROM payment_orders WHERE platform_trade_no = ?", (trade_no,)).fetchone():
                    raise HTTPException(400, "交易号已使用")
                db.execute("UPDATE payment_orders SET status = 'paid', platform_trade_no = ?, paid_at = ? WHERE id = ?",
                           (trade_no, now_iso(), order_id))
                db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (row["credits"], row["user_id"]))
                ledger_entry(db, row["user_id"], row["credits"], "payment", f"支付宝充值 ¥{cents / 100:.2f}", f"payment:{order_id}")
        return Response(content="success", media_type="text/plain")

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

    @app.get("/api/admin/users/{user_id}/records")
    def user_records(user_id: int, user=Depends(admin_user)):
        with database_connection(database_path) as db:
            target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not target:
                raise HTTPException(404, "用户不存在")
            jobs = db.execute("SELECT j.id, j.user_id, j.prompt, j.status, j.created_at, j.credits_cost, j.error_message, h.id AS image_id, h.prompt AS saved_prompt, u.username, u.email FROM generation_jobs j JOIN users u ON u.id = j.user_id LEFT JOIN image_history h ON h.id = j.id WHERE j.user_id = ? ORDER BY j.created_at DESC LIMIT 30", (user_id,)).fetchall()
            ledger = db.execute("SELECT amount, balance_after, kind, description, created_at FROM credit_ledger WHERE user_id = ? ORDER BY id DESC LIMIT 30", (user_id,)).fetchall()
            orders = db.execute("SELECT id, amount_cents, credits, status, created_at, paid_at FROM payment_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (user_id,)).fetchall()
        return {"user": public_user(target),
                "jobs": [admin_job_record(row) for row in jobs],
                "ledger": [dict(row) for row in ledger], "payment_orders": [dict(row) for row in orders]}

    @app.get("/api/admin/jobs")
    def list_admin_jobs(page: int = 1, status: str = "", user_id: int | None = None, user=Depends(admin_user)):
        if page < 1 or page > 100000 or status not in ("", "succeeded", "failed", "pending"):
            raise HTTPException(400, "筛选参数无效")
        where, params = [], []
        if status:
            where.append("j.status = ?")
            params.append(status)
        if user_id is not None:
            where.append("j.user_id = ?")
            params.append(user_id)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with database_connection(database_path) as db:
            rows = db.execute("SELECT j.id, j.user_id, j.prompt, j.status, j.created_at, j.credits_cost, j.error_message, h.id AS image_id, h.prompt AS saved_prompt, u.username, u.email FROM generation_jobs j JOIN users u ON u.id = j.user_id LEFT JOIN image_history h ON h.id = j.id" + clause + " ORDER BY j.created_at DESC, j.id DESC LIMIT 31 OFFSET ?", (*params, (page - 1) * 30)).fetchall()
        return {"items": [admin_job_record(row) for row in rows[:30]], "page": page, "has_more": len(rows) > 30}

    @app.get("/api/admin/images/files/{item_id}")
    def admin_image_file(item_id: str, user=Depends(admin_user)):
        if not re.fullmatch(r"[0-9a-f]{24}", item_id):
            raise HTTPException(404, "图片不存在")
        with database_connection(database_path) as db:
            item = db.execute("SELECT id FROM image_history WHERE id = ? AND status = 'succeeded'", (item_id,)).fetchone()
        if not item:
            raise HTTPException(404, "图片不存在")
        for extension in ("png", "jpg", "webp"):
            path = image_dir / f"{item_id}.{extension}"
            if path.is_file():
                return FileResponse(path, filename=f"astrogate-{item_id}.{extension}", content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})
        raise HTTPException(404, "图片不存在")

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
            rows = db.execute("SELECT id, prompt, model, size, quality, resolution, output_size, source_size, mode, reference_count, credits_cost, image_src, status, created_at FROM image_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 60", (user["id"],)).fetchall()
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

    def reserve(user_id: int, key: str, fingerprint: str, credits_cost: int, model: str, available: bool, prompt: str):
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
            if not available:
                raise HTTPException(503, "当前图片节点尚未开放该模型，请选择其他模型")
            if not db.execute("UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?", (credits_cost, user_id, credits_cost)).rowcount:
                raise HTTPException(402, f"积分不足，请先兑换充值；当前模型每张图片需要 {credits_cost} 积分")
            job_id = secrets.token_hex(12)
            db.execute("INSERT INTO generation_jobs (id, user_id, request_key, fingerprint, status, created_at, credits_cost, prompt) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)", (job_id, user_id, key, fingerprint, now_iso(), credits_cost, prompt))
            ledger_entry(db, user_id, -credits_cost, "generation", f"生成图片 · {model} · 1 张", f"generation:{job_id}")
            return job_id, None

    def refund(job_id: str, message: str):
        with database_connection(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT user_id, status, credits_cost FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if job and job["status"] == "pending":
                db.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (job["credits_cost"], job["user_id"]))
                ledger_entry(db, job["user_id"], job["credits_cost"], "refund", "生成失败，积分已退回", f"refund:{job_id}")
                db.execute("UPDATE generation_jobs SET status = 'failed', error_message = ? WHERE id = ?", (message[:500], job_id))

    async def run_generation(body, references, user, request_key):
        if not config.upstream_url or not config.upstream_api_key:
            raise HTTPException(503, "图片服务暂未配置")
        models = {item["id"]: item for item in await image_model_items()}
        selected_model = models[body.model]
        credits_cost = selected_model["credits_cost"]
        if len(references) > MAX_REFERENCES:
            raise HTTPException(400, "最多可使用 8 张参考图")
        decoded = [decode_reference(value) for value in references]
        if sum(size for _, size in decoded) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "参考图总大小不能超过 50MB")
        mode = "image-to-image" if references else "text-to-image"
        target = export_dimensions(body.size, body.resolution)
        upstream_size = f"{target[0]}x{target[1]}" if target else body.size
        payload = {"prompt": body.prompt, "model": body.model, "size": upstream_size, "quality": body.quality, "n": 1, "response_format": "b64_json", "stream": False}
        if references:
            payload["images"] = [item for item, _ in decoded]
        key = request_key or secrets.token_hex(16)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", key):
            raise HTTPException(400, "请求编号无效")
        fingerprint = hash_token(json.dumps(payload, sort_keys=True))
        job_id, previous = reserve(user["id"], key, fingerprint, credits_cost, body.model, selected_model["available"], body.prompt)
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
                            if len(chunks) > MAX_OUTPUT_BYTES:
                                raise HTTPException(502, "生成图片超过大小限制")
                    output = bytes(chunks)
                else:
                    raise HTTPException(502, "图片服务未返回图片")
            if len(output) > MAX_OUTPUT_BYTES:
                raise HTTPException(502, "生成图片超过大小限制")
            try:
                output, extension, output_size, source_size = prepare_generated_image(output, target)
            except HTTPException as exc:
                raise HTTPException(502, exc.detail) from exc
            output_path = image_dir / f"{job_id}.{extension}"
            output_path.write_bytes(output)
            item = {"id": job_id, "prompt": body.prompt, "model": body.model, "size": body.size, "quality": body.quality,
                    "resolution": body.resolution, "output_size": output_size, "source_size": source_size, "mode": mode, "reference_count": len(references), "credits_cost": credits_cost, "image_src": f"/api/images/files/{job_id}", "status": "succeeded", "created_at": now_iso()}
            with database_connection(database_path) as db:
                db.execute("INSERT INTO image_history (id, user_id, prompt, model, size, quality, resolution, output_size, source_size, mode, reference_count, credits_cost, image_src, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (job_id, user["id"], body.prompt, body.model, body.size, body.quality, body.resolution, output_size, source_size, mode, len(references), credits_cost, item["image_src"], "succeeded", item["created_at"]))
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
