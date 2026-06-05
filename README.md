<<<<<<< HEAD
# Quote Agent Assistant

这是一个“独立/自动报价助手”MVP，把 `/cost-model-skills` 里的 6 个 Markdown skill 包装成一个多 Agent 系统。

它现在同时支持：

- 本地命令行报价。
- 浏览器上传图纸报价。
- HTTP API 对外接入。
- 异步任务模式，适合接企业系统或前端页面。

## Agent 结构

```text
自动报价总控 Agent
├─ 图纸品类识别路由 Agent
├─ 铜铝排成本 Agent
├─ 铜编织线成本 Agent
├─ 绝缘纸成本 Agent
├─ 外六角大螺栓成本 Agent
├─ 钣金件成本 Agent
└─ 报价审核 Agent
```

## 安装

```
python -m venv .venv
./.venv/Scripts/Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`：

```text
# 审核模型和 PDF/文件上传默认使用这组 OpenAI-compatible 配置
OPENAI_API_KEY=你的 OpenAI-compatible key
OPENAI_BASE_URL=https://你的-openai-compatible地址/v1

# 工作模型：基于视觉摘要做路由、成本推理、初版报价
QUOTE_WORK_MODEL=mimo-tokenplan-model-name
QUOTE_WORK_BASE_URL=https://your-tokenplan-endpoint.example/v1
QUOTE_WORK_API_KEY=your-tokenplan-api-key

# 视觉模型：图片/PDF/附件事实提取；留空时默认使用审核模型
QUOTE_VISION_MODEL=gpt-5.5

# 审核模型：OpenAI 或 OpenAI-compatible
QUOTE_REVIEW_MODEL=gpt-5.5

# 可选：如果审核模型和 OPENAI_BASE_URL 不同，单独覆盖
QUOTE_REVIEW_BASE_URL=
QUOTE_REVIEW_API_KEY=
```

如果你的系统没有 `python` 命令，可以把上面的 `python` 换成你本机 Python 的完整路径。

## 模型配置

现在支持三个模型分工：

| 配置项 | 用途 |
|---|---|
| `QUOTE_VISION_MODEL` | 图片/PDF/附件识别，提取图纸事实摘要；留空时默认使用审核模型 |
| `QUOTE_VISION_BASE_URL` | 可选，单独覆盖视觉模型 baseURL |
| `QUOTE_VISION_API_KEY` | 可选，单独覆盖视觉模型 key |
| `QUOTE_WORK_MODEL` | 基于视觉摘要做品类路由、成本推理、初版报告；可以使用不支持图片的 Mimo/TokenPlan 文本模型 |
| `QUOTE_WORK_BASE_URL` | 工作模型的 OpenAI-compatible API 地址，例如 TokenPlan/Mimo 的 `/v1` endpoint |
| `QUOTE_WORK_API_KEY` | 工作模型 API key |
| `QUOTE_REVIEW_MODEL` | 审核 Agent 使用的 OpenAI/OpenAI-compatible 模型 |
| `QUOTE_REVIEW_ENDPOINT` | 审核模型调用方式，默认 `responses` 直连 `/v1/responses`；可设为 `chat` 使用 Chat Completions |
| `QUOTE_REVIEW_STREAM` | 审核模型走 `/v1/responses` 时是否启用流式，默认 `true` |
| `OPENAI_API_KEY` | 视觉和审核模型默认 key，也用于 PDF/非图片文件上传 |
| `OPENAI_BASE_URL` | 视觉和审核模型默认 OpenAI-compatible 地址，也用于 PDF/非图片文件上传 |
| `QUOTE_REVIEW_BASE_URL` | 可选，单独覆盖审核模型 baseURL |
| `QUOTE_REVIEW_API_KEY` | 可选，单独覆盖审核模型 key |
| `QUOTE_FILE_BASE_URL` | 可选，单独覆盖 PDF/文件上传 baseURL |
| `QUOTE_FILE_API_KEY` | 可选，单独覆盖 PDF/文件上传 key |

如果 `QUOTE_WORK_BASE_URL` 留空，工作模型会默认走 OpenAI。  
如果填写了 `QUOTE_WORK_BASE_URL`，系统会用 OpenAI-compatible Chat Completions 方式接入工作模型。

如果你的 OpenAI 不是官方地址，填写 `OPENAI_BASE_URL` 即可，例如：

```text
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=sk-...
QUOTE_REVIEW_MODEL=gpt-5.5
```

命令行也可以临时覆盖：

```
python -m quote_assistant --vision-model gpt-5.5 --work-model mimo-tokenplan-model-name --review-model gpt-5.5 quote "识别图纸并报价" --file /path/to/drawing.png
```

兼容旧参数：

```
python -m quote_assistant --model gpt-4.1-mini quote "识别图纸并报价"
```

注意：很多非 OpenAI Provider 不支持 OpenAI 的 `input_file` 文件 ID 或 tool calling。当前版本中，图片通常更容易通过 OpenAI-compatible 视觉接口处理；PDF 仍可能依赖 OpenAI 文件上传能力。如果 TokenPlan/Mimo 不支持工具调用，需要后续把成本公式改成纯 Python 前置计算，再把结果交给模型整理报告。

## 手动报价

文字参数：

```
python -m quote_assistant quote "帮我计算 T2 铜排 100x10x300mm 成本，铜含税单价 78 元/kg。"
```

图纸/PDF：

```
python -m quote_assistant quote "识别图纸品类并生成报价报告。铜含税单价 78 元/kg。" --file /path/to/drawing.pdf
```

图片：

```
python -m quote_assistant quote "请根据图纸做成本估算，缺少参数列待确认。" --file /path/to/drawing.png
```

默认会自动审核。如果审核不通过，系统会带着审核意见重新识别/重算，默认最多重跑 2 轮：

```
python -m quote_assistant quote "识别图纸品类并生成报价报告。" --file /path/to/drawing.pdf --max-review-rounds 3
```

需要在正式报告末尾显示审核记录时：

```
python -m quote_assistant quote "识别图纸品类并生成报价报告。" --file /path/to/drawing.pdf --audit
```

## 自动报价

启动监听：

```
python -m quote_assistant watch --inbox inbox --outbox outbox
```

之后把图纸 PDF / 图片放进 `inbox`，助手会自动在 `outbox` 生成 Markdown 报价报告。

自动模式同样支持审核循环：

```
python -m quote_assistant watch --inbox inbox --outbox outbox --max-review-rounds 3 --audit
```

## 审核闭环

每次报价都会经过：

```text
生成报价 -> 报价审核 Agent -> 通过则输出
                         └-> 不通过则带审核意见重新识别/重算
```

审核 Agent 会检查：

- 品类识别和派发是否正确。
- 图纸事实、推断、模板默认值、用户输入是否分离。
- 是否编造了尺寸、重量、工艺、材料牌号或实时市场价。
- 公式、单位、数量、税/未税口径是否自洽。
- 缺少关键参数时是否列为待确认，而不是输出伪确定价格。

如果超过最大重跑轮次仍未通过，系统不会把结果当作正式报价输出，而是生成“自动审核未通过”报告，列出问题和最后一版草稿。

## Web / API 服务

启动 Web/API：

```
python -m quote_assistant serve --host 0.0.0.0 --port 8000
```

浏览器访问：

```text
http://服务器IP:8000/
```

同步 API：

```text
POST /api/quote
```

异步 API：

```text
POST /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/report
```

详细部署和 API 示例见 [DEPLOY.md](DEPLOY.md)。

## 现在能做什么

- 自动识别：铜铝排、编织线、绝缘纸、大六角螺栓、钣金件。
- 自动派发到对应专业成本 Agent。
- 支持文字、图片、PDF/文件输入。
- 有基础计算工具：矩形件重量、大螺栓理论重量、通用成本分解。
- 输出经过自动审核的报价报告和待确认项。
- 支持工作模型和审核模型分离：工作模型可接 OpenAI-compatible Provider，审核模型可单独使用 OpenAI。

## 下一步建议

1. 接入你的 Excel 成本模板，生成 `.xlsx` 报价表。
2. 增加材料价格表或 ERP/数据库接口。
3. 做一个网页上传界面，给业务人员直接拖拽图纸。
4. 增加人工复核流：AI 报价后由工程/报价人员确认再归档。
=======
# QuoteAgentAssistant
智能报价
>>>>>>> c43c94ff133c4e0902fd8d6870dbc398bbd9e3bf
