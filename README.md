# AstroGate IMG

可自托管的 AI 图片创作平台，包含作品社区首页、邮箱注册登录、文生图、多图参考、个人作品库、积分中心和管理控制台。

线上地址：<https://img.astrogate.sbs>

## 功能

- 输入提示词生成图片，或通过参考图进行图生图。
- 点击上传、拖放图片、`Ctrl + V` / `⌘ + V` 粘贴，支持最多 8 张 PNG/JPG/WebP 参考图，合计不超过 50MB。
- GPT Image 2 每张消耗 1 积分（¥0.05）；GPT Image 2.5 接入图片节点后每张消耗 5 积分（¥0.25）。新用户默认 0 积分，使用兑换码充值。
- 发起请求时预留积分，成功后完成扣费；失败或服务重启中断时退款。
- 使用数据库事务防止并发透支，使用请求编号防止同一次生成重复扣费。
- 积分流水、单次兑换码、管理员加减积分和兑换码管理。
- 个人作品库图片通过登录权限校验；用户主动发布到社区后，生成独立的公开图片副本。
- 注册用户可免费发布图文或纯文字帖子，上传、拖入或粘贴最多 9 张图片，单张 10MB、合计 30MB；支持从个人作品库分享。
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

创作设置提供 13 种画幅比例：1:1、2:3、3:2、4:3、3:4、16:9、9:16、4:5、5:4、2:1、1:2、21:9、9:21，默认使用 16:9（1536×864），文生图和多图参考均可使用。创作页可编辑前置词、选择风格预设并填写“避免元素”，提交前会展示最终发送给模型的提示词。“避免元素”以自然语言引导模型，不是独立的负面提示词接口。

“AI 帮写”使用独立文字节点生成可编辑的提示词建议，图片生成仍使用原有图片节点。文字节点的地址、密钥和模型只在服务端 `.env` 中配置，浏览器和 Git 仓库不保存密钥。模型选择根据图片节点的 `/v1/models` 返回值显示；当前图片节点仅提供 `gpt-image-2`，2.5 系列在图片节点接入前不可选择。积分在提交时按所选模型预留，失败全额退回。

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

首页展示社区用户帖子和 15 张精选图片。精选图片统一署名“站长精选”，有独立参考提示词和标签。每张精选作品预置不同的示例赞数与 2～7 条示例评论，刷新时保持稳定，卡片明确标注示例互动；用户帖子从零开始累计真实互动。提示词根据画面编写，不宣称为原始生成提示词。科研类图片作为视觉示例展示。

访客可浏览图片与讨论、复制提示词、分享作品链接。登录用户可点赞和发表评论；点赞以用户和作品唯一配对，评论写入数据库。用户可删除自己的评论，管理员可删除任意用户评论。公开接口不返回评论者邮箱。

登录后点击首页右上角“发帖”，填写标题、正文、分类与标签，图片和提示词均可选填；支持纯文字帖子。也可在工作台结果或个人作品库点击“分享到社区”，在发布窗口确认后公开。首页“我的帖子”可查看本人发布的内容；帖子作者和管理员可以删除帖子及其讨论。

发帖不消耗积分，每位用户每分钟最多发布 3 篇。图片在服务端验证并重新编码为 WebP，去除原始元数据，限制 2400 万像素并生成预览。公开图片保存到 `data/community/`，个人作品库仍独立保存在 `data/images/`；删除个人原图不会删除已公开的副本，删除帖子会移除公开图片。

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
node tests/community-posts.test.cjs
node tests/admin.test.cjs
```

测试覆盖社区资料与示例互动、图文/纯文字发布、多图上传、粘贴与大小限制、作品库主动分享、个人帖子分页、点赞幂等、评论持久化、帖子和评论删除权限、公开资料隐私、分类搜索与登录返回；也覆盖邮箱验证、积分计费、失败退款、多图转发、并发防透支和个人作品隔离。
