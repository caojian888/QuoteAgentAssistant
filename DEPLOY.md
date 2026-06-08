# 部署说明

本文档记录 Quote Agent Assistant 的服务器部署约定。核心原则是把代码、配置、运行数据分开：

```text
/opt/quote-agent-assistant/              # 代码
/etc/quote-agent-assistant/.env          # 配置和密钥
/var/lib/quote-agent-assistant/data      # 上传文件、任务记录、报告、Excel、图片拆解
```

## 服务入口

- 浏览器上传页面：`GET /`
- 同步报价 API：`POST /api/quote`
- 异步任务 API：`POST /api/jobs`，再轮询 `GET /api/jobs/{job_id}`

## 环境文件加载顺序

程序启动时会按以下顺序加载 `.env`：

```text
1. QUOTE_ENV_FILE 指定的位置
2. /etc/quote-agent-assistant/.env
3. 项目根目录 .env
```

相对路径配置会按项目根目录解析，不按启动命令所在目录解析。因此：

```text
QUOTE_DATA_DIR=data
```

在本地开发时会稳定指向项目根目录下的 `data`。线上推荐使用绝对路径：

```text
QUOTE_DATA_DIR=/var/lib/quote-agent-assistant/data
QUOTE_INBOX_DIR=/var/lib/quote-agent-assistant/data/inbox
QUOTE_OUTBOX_DIR=/var/lib/quote-agent-assistant/data/outbox
```

## 服务器 .env 示例

`/etc/quote-agent-assistant/.env` 至少需要包含：

```text
OPENAI_API_KEY=your-openai-compatible-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint.example/v1

QUOTE_VISION_MODEL=gpt-5.5
QUOTE_WORK_MODEL=your-work-model
QUOTE_WORK_BASE_URL=https://your-work-model-endpoint.example/v1
QUOTE_WORK_API_KEY=your-work-model-key
QUOTE_REVIEW_MODEL=gpt-5.5

QUOTE_DATA_DIR=/var/lib/quote-agent-assistant/data
QUOTE_DATABASE_URL=postgresql://quote_agent:change-db-password@127.0.0.1:5432/quote_agent
QUOTE_AUTH_REQUIRED=true
QUOTE_ADMIN_USERNAME=admin
QUOTE_ADMIN_PASSWORD=change-admin-password
QUOTE_COOKIE_SECURE=true

QUOTE_PUBLIC_BASE_URL=https://cost.inferhub.tech
QUOTE_FEISHU_LOGIN_ENABLED=false

# 飞书机器人事件入口，先保持 false；合并部署后再在服务器 env 中启用。
QUOTE_FEISHU_BOT_ENABLED=false
QUOTE_FEISHU_EVENT_VERIFICATION_TOKEN=
QUOTE_FEISHU_EVENT_ENCRYPT_KEY=
```

## 迁移现有 .env 和 data

如果服务器上之前把 `.env` 和 `data` 放在项目目录里，可以按这个流程迁移：

```bash
systemctl stop quote-agent-assistant

mkdir -p /etc/quote-agent-assistant
mkdir -p /var/lib/quote-agent-assistant

mv /opt/quote-agent-assistant/.env /etc/quote-agent-assistant/.env
mv /opt/quote-agent-assistant/data /var/lib/quote-agent-assistant/data

chmod 600 /etc/quote-agent-assistant/.env
chmod -R 750 /var/lib/quote-agent-assistant/data
```

迁移后，项目目录里不应再保留 `.env` 和 `data`，避免以后 `git pull`、打包或备份代码时带上密钥和运行数据。

## systemd 服务建议

```ini
[Unit]
Description=Quote Agent Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/quote-agent-assistant
EnvironmentFile=/etc/quote-agent-assistant/.env
ExecStart=/opt/quote-agent-assistant/.venv/bin/python -m quote_assistant serve --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

更新服务文件后执行：

```bash
systemctl daemon-reload
systemctl restart quote-agent-assistant
systemctl status quote-agent-assistant --no-pager
```

## 部署代码

推荐流程：

```bash
cd /opt/quote-agent-assistant
git pull
python -m pip install -r requirements.txt
systemctl restart quote-agent-assistant
```

## 飞书机器人接入

本项目支持把报价流程接入飞书开放平台应用机器人。推荐先通过 GitHub 合并，再由 Jenkins 部署到服务器；不要直接在服务器手改代码。

飞书后台配置：

```text
1. 开启应用机器人能力。
2. 在事件订阅中配置请求地址：
   https://cost.inferhub.tech/api/feishu/events
3. 订阅接收消息事件 im.message.receive_v1。
4. 添加读取消息资源、发送消息等机器人所需权限，并发布应用版本给企业管理员审批。
```

服务器环境变量：

```text
QUOTE_FEISHU_BOT_ENABLED=true
QUOTE_FEISHU_APP_ID=cli_xxx
QUOTE_FEISHU_APP_SECRET=xxx
QUOTE_FEISHU_EVENT_VERIFICATION_TOKEN=xxx
QUOTE_FEISHU_EVENT_ENCRYPT_KEY=xxx
QUOTE_PUBLIC_BASE_URL=https://cost.inferhub.tech
```

启用机器人时，`QUOTE_FEISHU_EVENT_VERIFICATION_TOKEN` 或 `QUOTE_FEISHU_EVENT_ENCRYPT_KEY` 至少配置一个；生产环境推荐两个都配置。

员工使用流程：

```text
@报价助手 开启报价
直接上传 PDF / 图片 / 图纸文件
@报价助手 帮我报价
```

文件上传消息不会立即创建报价任务，只会加入当前会话；收到“帮我报价”后才会创建任务并回传摘要、完整报告链接和 Excel 下载链接。

## 验证

服务重启后，至少检查：

```bash
curl -I https://cost.inferhub.tech/
curl https://cost.inferhub.tech/api/jobs/history
systemctl status quote-agent-assistant --no-pager
journalctl -u quote-agent-assistant -n 80 --no-pager
```

还需要在浏览器里验证：

- 登录页面能打开。
- 历史记录能读取。
- 上传图纸后任务能创建。
- 报告、Excel 下载、图片预览能打开。

## 生产注意事项

- 不要提交 `.env`、`data`、上传图纸、生成文件或密钥。
- `/etc/quote-agent-assistant/.env` 要限制权限。
- `/var/lib/quote-agent-assistant/data` 要纳入备份。
- 对公网服务建议放在 Nginx/Caddy 后面，并开启 HTTPS。
- 大并发时应引入队列，不要长期依赖进程内后台任务。
- 高金额报价建议保留人工复核流程。
