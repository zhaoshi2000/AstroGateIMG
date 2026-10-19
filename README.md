# AstroGate IMG

可自托管的 AI 图片创作平台，包含官网首页、注册登录、文生图、多图参考、个人作品库、积分中心和管理控制台。

线上地址：<https://img.astrogate.sbs>

## 功能

- 输入提示词生成图片，或通过参考图进行图生图。
- 点击上传、拖放图片、`Ctrl + V` / `⌘ + V` 粘贴，支持最多 8 张 PNG/JPG/WebP 参考图，合计不超过 50MB。
- 1 积分生成 1 张图片，标价 ¥0.05 RMB / 张。新用户默认 0 积分，使用兑换码充值。
- 发起请求时预留积分，成功后完成扣费；失败或服务重启中断时退款。
- 使用数据库事务防止并发透支，使用请求编号防止同一次生成重复扣费。
- 积分流水、单次兑换码、管理员加减积分和兑换码管理。
- 图片通过登录权限校验后访问，每位用户只能查看自己的作品。
- 使用邮箱与验证码注册、邮箱和密码登录；保留现有账号登录。验证码 10 分钟有效、60 秒重发间隔，支持错误次数和发信频率限制。
- 密码使用 PBKDF2-SHA256 哈希，会话使用 HttpOnly Cookie。验证码使用 HMAC 保存，注册成功后立即失效。

## 本地启动

```powershell
git clone https://github.com/zhaoshi2000/AstroGateIMG.git
cd AstroGateIMG
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写上游接口地址、密钥，以及初始化管理员账号：

```dotenv
ASTROGATE_UPSTREAM_URL=http://127.0.0.1:21873
ASTROGATE_UPSTREAM_API_KEY=your-upstream-api-key
ASTROGATE_ADMIN_USERNAME=your-admin-name
ASTROGATE_ADMIN_PASSWORD=your-admin-password
ASTROGATE_COOKIE_SECURE=false
ASTROGATE_SIGNUP_CREDITS=0
ASTROGATE_SMTP_HOST=127.0.0.1
ASTROGATE_SMTP_PORT=25
ASTROGATE_SMTP_SECURITY=none
ASTROGATE_SMTP_SENDER=noreply@astrogate.sbs
```

管理员账号首次启动时创建；已有账号不会被环境变量重置密码。

注册需要能发信的 SMTP 服务。当前生产环境使用同机 Postfix（127.0.0.1:25）发送 `noreply@astrogate.sbs` 的验证码邮件。使用其他邮箱供应商时填写 `ASTROGATE_SMTP_USERNAME`、`ASTROGATE_SMTP_PASSWORD`，并按供应商要求选择 `starttls`（一般 587）或 `ssl`（一般 465）。HMAC 密钥可通过 `ASTROGATE_EMAIL_CODE_SECRET` 设置，留空时自动生成到数据目录并持久保存。

同机 Postfix 需要在 `smtpd_relay_restrictions` 和 `smtpd_recipient_restrictions` 中允许 `permit_mynetworks`，且放在 `reject_unauth_destination` 前面；`mynetworks` 保持为本机回环地址。部署后需要向外部邮箱实际投递验证，本域邮箱投递成功不能证明外部投递可用。

验证码按钮显示剩余重发秒数，刷新页面或切换邮箱后仍保留对应邮箱的倒计时。发送受限或发送失败时，页面按服务器返回的 `Retry-After` 更新等待时间。

```powershell
.\run.ps1
```

打开 <http://127.0.0.1:43921>。可通过 `./run.ps1 -Port 43922` 修改本地端口。

## 主要页面

| 路径 | 用途 |
| --- | --- |
| `/` | 官网首页、功能、价格与常见问题 |
| `/register`、`/login` | 邮箱验证码注册、邮箱或已有账号登录 |
| `/studio` | 图片创作和个人作品库 |
| `/wallet` | 积分余额、兑换码充值、积分流水 |
| `/admin` | 管理员用户积分调整和兑换码管理 |

## 积分运营

管理员登录后打开 `/admin`，在“生成兑换码”中填写单码积分、数量和有效天数。完整兑换码只在创建时返回，请复制保存后交付给用户。用户在 `/wallet` 输入兑换码，积分立即到账。

管理员也可以在用户列表直接加减积分，输入正数增加、负数扣减，调整会写入流水。删除作品不会退还已消耗积分。

## Linux / Docker 部署

准备 `.env`，公网 HTTPS 环境设置 `ASTROGATE_COOKIE_SECURE=true`。创建数据目录并给予容器用户权限：

```sh
mkdir -p data
chown 10001:10001 data
docker compose up -d --build
```

应用默认仅监听 `127.0.0.1:43921`。使用 nginx 等反向代理提供 HTTPS，图片上传限制至少 75MB、请求超时至少 300 秒。`compose.yml` 使用 Linux host 网络，可以连接同机仅监听回环地址的图片上游。

部署目录中的 `data/astrogate.db` 保存用户、会话、积分、兑换码和流水；`data/images/` 保存生成图片。请为整个 `data/` 目录配置持久化和备份。应用目前必须使用一个 worker；进程启动会退款此前中断的生成任务。

`deploy/configure_ingress.py` 用于当前服务器的 HAProxy + nginx 结构，会备份并校验配置后添加 `img.astrogate.sbs` 的独立路由，不适用于任意服务器配置。

## 上游要求

使用 OpenAI 兼容图片接口：文生图为 `/v1/images/generations`，图生图为 `/v1/images/edits`。图生图使用 JSON `images` 数组（包含 `b64_json`、`filename`、`mime_type`），适配本项目正在使用的 `chatgpt2api`。上游密钥只保留在服务端。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app/static/app.js
node tests/clipboard.test.cjs
node tests/email-code.test.cjs
```

测试覆盖邮箱验证码注册、过期及错误次数、重发失效、邮箱大小写、已有账号兼容、零余额拦截、多图转发、扣费与幂等、失败退款、并发防透支、兑换码一次性、管理员权限、用户作品隔离和中断任务退款。
