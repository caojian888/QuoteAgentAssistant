# Quote Agent Assistant

Quote Agent Assistant is a multi-agent drawing quotation assistant. It routes uploaded
drawings or text requirements to costing skills, generates a quotation report, and runs
an automated review loop before returning final output.

## What It Supports

- Browser upload page for drawings, PDFs, and images.
- Synchronous quotation API: `POST /api/quote`.
- Asynchronous job API: `POST /api/jobs`, then poll `GET /api/jobs/{job_id}`.
- Local CLI quotation and folder watch mode.
- Separate vision, work, and review model configuration.
- Excel output, row-image evidence, audit records, login/history, and optional RAG.

## Agent Flow

```text
Quote controller agent
├─ Drawing material routing agent
├─ Copper/aluminum busbar costing agent
├─ Copper braided wire costing agent
├─ Insulation paper costing agent
├─ Large hex bolt costing agent
├─ Sheet metal costing agent
└─ Quotation review agent
```

## Project Layout

```text
quote_assistant/         Core application code, API handlers, agents, and workflows
templates/               HTML templates used by the web interface
static/agent-office/     Frontend assets for the browser upload experience
skills/                  Costing skill prompts and routing references
requirements.txt         Runtime dependency list
DEPLOY.md                Deployment notes and environment-specific path guidance
```

## Setup

Create a virtual environment, install dependencies, and create a local `.env` from
`.env.example`.

```text
python -m venv .venv
python -m pip install -r requirements.txt
```

Fill in at least one model API key in `.env`. The most important settings are:

```text
OPENAI_API_KEY=your-openai-compatible-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint.example/v1

QUOTE_VISION_MODEL=gpt-5.5
QUOTE_WORK_MODEL=your-work-model
QUOTE_WORK_BASE_URL=https://your-work-model-endpoint.example/v1
QUOTE_WORK_API_KEY=your-work-model-key
QUOTE_REVIEW_MODEL=gpt-5.5
```

## Runtime Paths

The application loads runtime configuration in this order:

```text
1. QUOTE_ENV_FILE, when explicitly set
2. Production env file, when present
3. Project-root .env for local development
```

`QUOTE_DATA_DIR`, `QUOTE_INBOX_DIR`, and `QUOTE_OUTBOX_DIR` may be absolute paths or
relative paths. Relative paths are always resolved from the project root, not from the
directory used to start the process.

Server-specific paths and migration steps are documented in `DEPLOY.md`.

## Run The Web Service

```text
python -m quote_assistant serve --host 0.0.0.0 --port 8000
```

Open:

```text
http://server-address:8000/
```

## Run A Manual Quote

```text
python -m quote_assistant quote "识别图纸品类并生成报价报告，缺少参数列为待确认。" --file path/to/drawing.pdf
```

Add automatic audit details to the report:

```text
python -m quote_assistant quote "识别图纸品类并生成报价报告。" --file path/to/drawing.png --audit
```

## Watch A Folder

```text
python -m quote_assistant watch
```

By default, watch mode reads `QUOTE_INBOX_DIR` and writes `QUOTE_OUTBOX_DIR`; if those
are not configured, it uses project-root `inbox` and `outbox`.

## API

Synchronous:

```text
POST /api/quote
```

Asynchronous:

```text
POST /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/report
GET /api/jobs/{job_id}/excel
GET /api/jobs/{job_id}/assets
```

## Notes

- Do not commit `.env`, `data`, `inbox`, `outbox`, uploaded drawings, generated Excel
  files, or private keys.
- Production deployments should keep code, configuration, and runtime data separated.
- High-value quotations should still include human review before customer delivery.
