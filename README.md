# Quote Agent Assistant

Quote Agent Assistant 是一个多智能体图纸报价助手。它会将上传的图纸或文本需求
路由到对应的成本核算技能，生成报价报告，并在返回最终结果前执行自动化审核闭环。

## 支持内容

- 用于上传图纸、PDF 和图片的浏览器页面。
- 同步报价 API：`POST /api/quote`。
- 异步任务 API：`POST /api/jobs`，然后轮询 `GET /api/jobs/{job_id}`。
- 本地 CLI 报价与文件夹监听模式。
- 独立的 vision、work 和 review 模型配置。
- Excel 输出、行图像证据、审核记录、登录/历史，以及可选 RAG。

## 智能体流程

```text
报价控制智能体
├─ 图纸物料路由智能体
├─ 铜/铝母排成本核算智能体
├─ 铜编织线成本核算智能体
├─ 绝缘纸成本核算智能体
├─ 大六角螺栓成本核算智能体
├─ 钣金件成本核算智能体
└─ 报价审核智能体
```

## 项目结构

```text
quote_assistant/         核心应用代码、API 处理器、智能体与工作流
templates/               Web 界面使用的 HTML 模板
static/agent-office/     浏览器上传页面使用的前端资源
skills/                  成本核算技能提示词与路由参考
requirements.txt         运行时依赖清单
DEPLOY.md                部署说明与环境路径指引
```

## 环境准备

先创建虚拟环境、安装依赖，并根据 `.env.example` 生成本地 `.env`。

```text
python -m venv .venv
python -m pip install -r requirements.txt
```

在 `.env` 中至少填写一组模型 API key。最重要的配置如下：

```text
OPENAI_API_KEY=your-openai-compatible-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint.example/v1

QUOTE_VISION_MODEL=gpt-5.5
QUOTE_WORK_MODEL=your-work-model
QUOTE_WORK_BASE_URL=https://your-work-model-endpoint.example/v1
QUOTE_WORK_API_KEY=your-work-model-key
QUOTE_REVIEW_MODEL=gpt-5.5
```

## 运行路径

应用会按以下顺序加载运行配置：

```text
1. 显式设置的 QUOTE_ENV_FILE
2. 存在时使用的生产环境 env 文件
3. 项目根目录下供本地开发使用的 .env
```

`QUOTE_DATA_DIR`、`QUOTE_INBOX_DIR` 和 `QUOTE_OUTBOX_DIR` 可以使用绝对路径，
也可以使用相对路径。相对路径始终以项目根目录为基准解析，而不是以启动进程时所在的
目录为基准。

服务端专用路径和迁移步骤记录在 `DEPLOY.md` 中。

## 启动 Web 服务

```text
python -m quote_assistant serve --host 0.0.0.0 --port 8000
```

打开：

```text
http://server-address:8000/
```

## 手动执行报价

```text
python -m quote_assistant quote "识别图纸品类并生成报价报告，缺少参数列为待确认。" --file path/to/drawing.pdf
```

如需在报告中加入自动审核详情：

```text
python -m quote_assistant quote "识别图纸品类并生成报价报告。" --file path/to/drawing.png --audit
```

## 监听文件夹

```text
python -m quote_assistant watch
```

默认情况下，监听模式会读取 `QUOTE_INBOX_DIR` 并写入 `QUOTE_OUTBOX_DIR`；如果
没有配置这两个变量，则会使用项目根目录下的 `inbox` 和 `outbox`。

## API

同步接口：

```text
POST /api/quote
```

异步接口：

```text
POST /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/report
GET /api/jobs/{job_id}/excel
GET /api/jobs/{job_id}/assets
```

## 注意事项

- 不要提交 `.env`、`data`、`inbox`、`outbox`、上传的图纸、生成的 Excel 文件，
  以及私钥。
- 生产环境部署应将代码、配置和运行数据分离。
- 高价值报价在交付客户前仍应保留人工复核。
