# AstroGate IMG

可自托管的 AI 图片创作平台，包含作品社区首页、邮箱注册登录、文生图、多图参考、个人作品库、积分中心和管理控制台。

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

创作设置提供 13 种画幅比例：1:1、2:3、3:2、4:3、3:4、16:9、9:16、4:5、5:4、2:1、1:2、21:9、9:21，文生图和多图参考均可使用。

| 路径 | 用途 |
| --- | --- |
| `/` | 作品社区、分类搜索、作者署名、参考提示词、点赞和评论 |
| `/register`、`/login` | 邮箱验证码注册、邮箱或已有账号登录 |
| `/studio` | 图片创作和个人作品库 |
| `/wallet` | 积分余额、兑换码充值、积分流水 |
| `/admin`、`/admin/login` | 独立的管理员登录与运营概览 |
| `/admin/accounts` | 上游账号池、剩余图片额度与上游刷新 |
| `/admin/users`、`/admin/codes` | 用户积分调整与兑换码管理 |

## 作品社区

首页展示 15 张用户提供的图片，按动漫插画、创意拼贴、科研可视化分类，统一署名“站长精选”。每张作品有独立的参考提示词、标签和两条明确标注的示例评论。提示词根据画面编写，不宣称为原始生成提示词。科研类图片作为视觉示例展示。

访客可浏览图片与讨论、复制提示词、分享作品链接。登录用户可点赞和发表评论；点赞以用户和作品唯一配对，评论写入数据库。用户可删除自己的评论，管理员可删除任意用户评论。公开接口不返回评论者邮箱。

“用此提示词创作”把提示词与画幅保存在当前浏览器会话中，进入工作台后带入；用户手动点击生成才会发起出图。登录后可返回之前查看的作品讨论。

作品资料位于 `app/gallery_catalog.json`，网页图片位于 `app/static/gallery/`。需要重新处理源图片时，安装 Pillow 后执行 `python scripts/prepare_gallery_assets.py D:/Down`；脚本生成完整画幅和预览 WebP，保持构图并避免首页加载原始 PNG。

## 积分运营

管理端使用独立的 `admin.html`、`admin.css` 和 `admin.js`，与客户工作台分开。管理员登录后默认进入 `/admin`，普通客户无法访问管理数据。后台显示上游账号的邮箱、套餐、状态、剩余图片次数、重置时间和成功/失败次数，支持按账号筛选及手动刷新额度。

上游凭据只在服务端使用；管理页面接收的是白名单字段，不返回 access token、refresh token 或内部上游密钥。同一账号的多条凭据合并显示，汇总仅累计正常账号的剩余图片额度。页面每 30 秒读取一次当前额度，点击刷新按钮会启动上游实时查询并显示进度。

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
node tests/community.test.cjs
node tests/admin.test.cjs
```

测试覆盖 15 张社区作品的资料完整性、点赞幂等、评论持久化与删除权限、公开资料隐私、分类与搜索、提示词带入与安全登录返回；也覆盖邮箱验证、积分计费、失败退款、多图转发、并发防透支和个人作品隔离。
