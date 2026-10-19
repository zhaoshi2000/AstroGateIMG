# AstroGate IMG

AstroGate IMG 是一个可自托管的图像创作客户端。用户注册后拥有独立会话和个人图集；应用通过服务端连接 OpenAI 兼容的图片接口，例如 `chatgpt2api`。

## 包含功能

- 账号注册、登录和退出，密码使用 PBKDF2-SHA256 哈希保存。
- 仅使用 HttpOnly 会话 Cookie，登录状态有效期为 14 天。
- 图片提示词、模型、画幅和质量选择。
- 上传 PNG、JPG 或 WebP 参考图，按提示词进行图生图；上传图片最大 50MB。
- 每位用户独立保存和删除自己的生成记录。
- 深色创作工作台，适配桌面和手机浏览器。
- 上游接口地址和密钥仅保存在服务端环境变量中，不会发送给浏览器。

## 快速启动（Windows）

```powershell
git clone https://github.com/zhaoshi2000/AstroGateIMG.git
cd AstroGateIMG
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写图片接口地址和可用的 API 密钥：

```dotenv
ASTROGATE_UPSTREAM_URL=http://127.0.0.1:21873
ASTROGATE_UPSTREAM_API_KEY=your-upstream-api-key
```

启动客户端服务：

```powershell
.\run.ps1
```

浏览器打开 [http://127.0.0.1:43921](http://127.0.0.1:43921)。默认端口为 `43921`，可用 `./run.ps1 -Port 43922` 修改。

## 配置项

| 变量 | 说明 |
| --- | --- |
| `ASTROGATE_UPSTREAM_URL` | OpenAI 兼容接口的根地址，例如 `http://127.0.0.1:21873`。 |
| `ASTROGATE_UPSTREAM_API_KEY` | 上游接口的 API 密钥。 |
| `ASTROGATE_DATA_DIR` | SQLite 数据目录，默认 `./data`。 |
| `ASTROGATE_COOKIE_SECURE` | HTTPS 部署时设为 `true`。本地 HTTP 使用 `false`。 |

文生图请求转发至 `POST {ASTROGATE_UPSTREAM_URL}/v1/images/generations`，图生图请求转发至 `POST {ASTROGATE_UPSTREAM_URL}/v1/images/edits`。在工作台填写提示词后，可直接生成；添加参考图后则切换为图生图。结果会保存到当前账号的个人图集。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖注册、重复账号拦截、登录、退出、会话校验、文生图、图生图和参考图格式校验。

## 部署说明

部署到公网时，在 HTTPS 反向代理后运行应用，并设置 `ASTROGATE_COOKIE_SECURE=true`。应用的数据保存在 `ASTROGATE_DATA_DIR` 指定的目录，部署时请为该目录配置持久化存储。
