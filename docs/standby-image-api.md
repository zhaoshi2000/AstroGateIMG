# 82 服务器图片 API

API 基址为 `https://82.157.64.38:18448/v1`。在 [API 管理页](https://82.157.64.38:18448/admin/api) 创建图片 API 密钥。以下四个接口都使用请求头 `Authorization: Bearer <图片 API 密钥>`。完整请求和响应结构见 [OpenAPI](https://82.157.64.38:18448/openapi.json)。

| 方法 | 路径（接在基址后） | 用途 |
| --- | --- | --- |
| GET | `/models` | 查询实际模型 ID、可用状态和每张积分 |
| POST | `/images/generations` | 根据提示词生成一张图 |
| POST | `/images/edits` | 根据提示词和参考图生成一张图 |
| GET | `/images/files/{item_id}` | 用同一管理员的密钥下载图片 |

## 用 PowerShell 生成并下载

把图片 API 密钥放入当前进程的环境变量 `ASTROGATE_IMG_KEY`。下面示例用同一把密钥生成和下载，在当前目录保存结果。

```powershell
$base = 'https://82.157.64.38:18448/v1'
$key = $env:ASTROGATE_IMG_KEY
if (-not $key) { throw '请先设置 ASTROGATE_IMG_KEY' }
$headers = @{ Authorization = "Bearer $key" }
(Invoke-RestMethod "$base/models" -Headers $headers).data | Select-Object id, available, credits_cost

$headers['Idempotency-Key'] = [guid]::NewGuid().ToString()
$params = @{
  model = 'gpt-image-2'
  prompt = '一只蓝色纸船漂浮在水面上，极简插画'
  size = '1536x864'
  response_format = 'url'
}
$body = [Text.Encoding]::UTF8.GetBytes(($params | ConvertTo-Json -Compress))
$result = Invoke-RestMethod "$base/images/generations" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 960
Invoke-WebRequest "$base/images/files/$($result.id)" -Headers @{ Authorization = "Bearer $key" } -OutFile ".\$($result.id).png" -TimeoutSec 120
$result | Select-Object id, model, output_size, credits_cost, credits
```

## 用两张参考图生成

沿用上面的 `$base`、`$key`，将两张 PNG 放在当前目录。请求使用 JSON，参考图按数组顺序编号。JPG 和 WebP 分别使用 `image/jpeg` 和 `image/webp`。

```powershell
$refs = @('主体.png', '风格.png') | ForEach-Object {
  $bytes = [IO.File]::ReadAllBytes((Resolve-Path $_).Path)
  'data:image/png;base64,' + [Convert]::ToBase64String($bytes)
}
$headers = @{ Authorization = "Bearer $key"; 'Idempotency-Key' = [guid]::NewGuid().ToString() }
$params = @{ model = 'gpt-image-2'; prompt = '保留图一的主体，使用图二的水彩风格'; image_data_urls = @($refs); response_format = 'url' }
$body = [Text.Encoding]::UTF8.GetBytes(($params | ConvertTo-Json -Depth 4 -Compress))
$result = Invoke-RestMethod "$base/images/edits" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 960
Invoke-WebRequest "$base/images/files/$($result.id)" -Headers @{ Authorization = "Bearer $key" } -OutFile ".\$($result.id).png" -TimeoutSec 120
```

## 参数、并发与重试

| 参数 | 用法 |
| --- | --- |
| `model` | 标准档为 `gpt-image-2`，网页显示“GPT Image 2.5 · 标准”，每张 1 积分。其他档位请先查 `GET /models` 的 `available`。 |
| `prompt` | 必填，1–4000 字符。 |
| `size` | 默认 `1536x864`（16:9），另支持 `1024x1024`、`1024x1536`、`1536x1024`、`1280x960`、`960x1280`、`864x1536`、`1024x1280`、`1280x1024`、`1536x768`、`768x1536`、`1792x768`、`768x1792`。 |
| `quality` | `low`、`medium`（默认）、`high`。 |
| `resolution` | 默认 `original`。若选 `1K`、`2K`、`3K`、`4K`，导出时按所选比例缩放，最长边分别为 1024、2048、3072、3840 像素。 |
| `image_data_urls` | 图生图必填，可传 1–8 张 PNG、JPG 或 WebP，解码后合计最多 50 MB。 |
| `response_format` | 默认 `b64_json`，响应在 `data[0].b64_json` 中返回图片。使用 `url` 时，下载 `data[0].url` 需要携带 Bearer 密钥。 |
| `n` | 每次为 1。多张图发多个请求，并发受密钥设置及同一管理员合计 8 个任务的上限约束。 |

每个新任务使用独立的 `Idempotency-Key`。连接中断时，先看 [82 机作品历史](https://82.157.64.38:18448/studio)，重试同一任务时使用相同的请求体、编号和服务器。两台服务器各自记录任务编号，跨站重试可能重复出图和计费。

| HTTP 状态 | 处理 |
| --- | --- |
| 401 / 402 | 分别检查图片 API 密钥和该管理员的积分余额。 |
| 409 | 检查已有任务及其结果；同一编号不能用于另一组参数。 |
| 429 | 按 `Retry-After` 等待后重试。 |
| 502 / 503 | 检查任务记录和模型可用状态，再决定是否重试。 |

## 图片保留时间

每月 1 日北京时间 03:15，服务会从个人作品历史及 API 下载接口清理上一个自然月之前的生成图片。例如 12 月 1 日清理 11 月 1 日零点以前的图片。需要长期保存的原图请提前下载，已发布的社区帖子保留独立副本。

## 上游授权账号

82 机约每两分钟从图片主机拉取快照，校验通过后，将主站托管账号的新增、凭据轮换及移除同步到 82 的生图服务。调用方只需自己的图片 API 密钥。主机断连期间，82 可继续使用本机仍有效且有额度的授权，新增与轮换会在同步链路恢复后更新。
