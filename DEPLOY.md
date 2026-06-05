# 部署说明

当前项目已经不是只能本地 CLI 使用。它有三种入口：

- 浏览器上传页面：`GET /`
- 同步报价 API：`POST /api/quote`
- 异步任务 API：`POST /api/jobs`，再轮询 `GET /api/jobs/{job_id}`

## 本机或内网启动

```powershell
cd C:\Users\YWsensei\Documents\Codex\2026-06-03\agent-2\outputs\quote-agent-assistant
.\.venv\Scripts\Activate.ps1
python -m quote_assistant serve --host 0.0.0.0 --port 8000
```

浏览器访问：

```text
http://服务器IP:8000/
```

如果只想本机访问：

```powershell
python -m quote_assistant serve --host 127.0.0.1 --port 8000
```

## 安全配置

`.env` 中设置：

```text
```

设置后，API 请求需要带其中一种：

```text
```

浏览器上传页面使用登录会话鉴权；如果要给多人用，建议保留登录系统并按用户隔离历史记录。

## 模型 BaseURL

如果你的 OpenAI 不走官方地址，在服务器上编辑：

```bash
nano /opt/quote-agent-assistant/.env
```

填写：

```text
OPENAI_API_KEY=你的 OpenAI-compatible key
OPENAI_BASE_URL=https://你的-openai-compatible地址/v1
QUOTE_VISION_MODEL=gpt-5.5
QUOTE_REVIEW_MODEL=gpt-5.5

QUOTE_WORK_MODEL=你的 mimo tokenplan 模型名
QUOTE_WORK_BASE_URL=你的 mimo tokenplan /v1 地址
QUOTE_WORK_API_KEY=你的 mimo tokenplan key
```

如果审核模型和文件上传不是同一个接口，可以额外使用：

```text
QUOTE_REVIEW_BASE_URL=
QUOTE_REVIEW_API_KEY=
QUOTE_FILE_BASE_URL=
QUOTE_FILE_API_KEY=
```

改完后重启：

```bash
systemctl restart quote-agent-assistant
```

## API 示例

同步接口：

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/quote `
  -F "prompt=识别图纸品类并生成报价报告，缺少参数列为待确认。" `
  -F "files=@C:\path\to\drawing.png" `
  -F "max_review_rounds=2" `
  -F "audit=true"
```

异步接口：

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/jobs `
  -F "prompt=识别图纸品类并生成报价报告。" `
  -F "files=@C:\path\to\drawing.pdf"
```

返回 `job_id` 后查询：

```powershell
curl.exe http://127.0.0.1:8000/api/jobs/JOB_ID
curl.exe http://127.0.0.1:8000/api/jobs/JOB_ID/report
```

## Docker 部署

```powershell
docker build -t quote-agent-assistant .
docker run --env-file .env -p 8000:8000 quote-agent-assistant
```

## 真正生产化还需要

- HTTPS：用 Nginx/Caddy/云负载均衡加 TLS。
- 登录权限：不要只依赖一个共享 token 给公网用户。
- 文件存储：把 `QUOTE_DATA_DIR` 放到持久化磁盘或对象存储。
- 队列：大量并发时用 Redis/Celery/RQ，而不是进程内后台任务。
- 审计：保存输入图纸、模型版本、审核记录、最终报价版本。
- 人工复核：金额或风险超过阈值时进入人工确认流。
