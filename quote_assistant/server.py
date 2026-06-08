from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .app import run_quote
from .db import (
    SESSION_COOKIE_NAME,
    AuthContext,
    admin_soft_delete_job,
    authenticate_user,
    count_jobs_for_user,
    cookie_secure,
    create_session,
    db_enabled,
    find_or_create_oauth_user,
    get_job_feedback,
    get_session_user,
    hide_job_for_user,
    init_db,
    job_admin_deleted_at,
    job_owner,
    list_jobs_for_user,
    replace_job_assets,
    replace_job_files,
    revoke_session,
    session_ttl_days,
    upsert_job_feedback,
    upsert_job,
)
from .external_rag import sync_feedback_document_to_rag
from .feishu_auth import (
    FeishuAuthError,
    build_feishu_authorize_url,
    feishu_default_role,
    feishu_login_enabled,
    fetch_feishu_user_profile,
)
from .feishu_bot import (
    FeishuBotAction,
    FeishuDownloadedFile,
    build_feishu_bot_action,
    decode_feishu_event,
    download_feishu_attachments,
    ensure_feishu_event_user,
    feishu_bot_audit_enabled,
    feishu_bot_config_status,
    feishu_bot_enabled,
    feishu_bot_max_review_rounds,
    feishu_challenge_response,
    is_duplicate_feishu_event,
    quote_completed_text,
    quote_created_text,
    send_feishu_file,
    send_feishu_report_messages,
    send_feishu_text,
)
from .model_config import build_model_config
from .office_events import bind_office_event_context, log_office_event, read_office_events, reset_office_event_context
from .office_state import build_office_state
from .qc import build_prompt_with_vision_context, extract_vision_context, generate_once, review_once
from .runtime_config import PROJECT_ROOT, load_runtime_env, runtime_data_dir


load_runtime_env()
DATA_DIR = runtime_data_dir()
JOBS_DIR = DATA_DIR / "jobs"
STATIC_DIR = PROJECT_ROOT / "static"
OFFICE_PAGE_PATH = PROJECT_ROOT / "templates" / "agent-office.html"
OFFICE_ASSET_PATHS = (
    OFFICE_PAGE_PATH,
    STATIC_DIR / "agent-office" / "styles.css",
    STATIC_DIR / "agent-office" / "agent-office.config.js",
    STATIC_DIR / "agent-office" / "script.js",
)
FEISHU_STATE_COOKIE_NAME = "quote_feishu_oauth_state"
FEISHU_NEXT_COOKIE_NAME = "quote_feishu_oauth_next"
logger = logging.getLogger("uvicorn.error")


@dataclass
class JobRecord:
    job_id: str
    status: str
    created_at: str
    updated_at: str
    prompt: str
    user_id: int | None = None
    report_path: str | None = None
    error: str | None = None
    review_status: str | None = None
    review_path: str | None = None
    review_error: str | None = None
    assets_path: str | None = None


jobs: dict[str, JobRecord] = {}


@dataclass
class SavedUpload:
    path: Path
    original_name: str
    stored_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int | None = None


app = FastAPI(
    title="Quote Agent Assistant",
    description="Multi-agent drawing quotation assistant with review loop.",
    version="0.1.0",
)


cors_origins = [origin.strip() for origin in os.getenv("QUOTE_CORS_ORIGINS", "").split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
async def startup() -> None:
    try:
        init_db()
        if db_enabled():
            backfill_job_files_from_disk()
            logger.info("quote database initialized")
    except Exception:
        logger.exception("quote database initialization failed")
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def auth_required() -> bool:
    if not db_enabled():
        return False
    value = os.getenv("QUOTE_AUTH_REQUIRED", "true").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def public_base_url(request: Request) -> str:
    configured = os.getenv("QUOTE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}".rstrip("/")


def feishu_redirect_uri(request: Request) -> str:
    configured = os.getenv("QUOTE_FEISHU_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return f"{public_base_url(request)}/auth/feishu/callback"


def safe_next_url(value: str | None, default: str = "/") -> str:
    candidate = (value or "").strip()
    if not candidate:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\\" in candidate or any(character in candidate for character in "\r\n\0"):
        return default
    return candidate


def login_redirect(next_url: str = "/") -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(safe_next_url(next_url), safe='')}", status_code=303)


def current_auth(request: Request) -> AuthContext | None:
    session_auth = get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_auth:
        return session_auth

    return None


def require_token(request: Request) -> AuthContext:
    auth = current_auth(request)
    if auth:
        return auth

    if not auth_required():
        return AuthContext(user_id=None, username="anonymous", role="anonymous")

    raise HTTPException(status_code=401, detail="Login required.")


def require_job_access(request: Request, job_id: str) -> AuthContext:
    auth = require_token(request)
    if auth.role == "admin" or auth.user_id is None:
        return auth

    owner_id = job_owner(job_id)
    if owner_id is None and db_enabled():
        try:
            owner_id = read_status(job_id).user_id
        except HTTPException:
            raise
        except Exception:
            owner_id = None

    if owner_id is None and db_enabled():
        raise HTTPException(status_code=404, detail="Job not found.")

    if owner_id is not None and owner_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Job not found.")
    return auth


def safe_name(file_name: str | None) -> str:
    raw = Path(file_name or "upload.bin").name
    clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", raw).strip("._")
    return clean or "upload.bin"


def office_asset_version() -> str:
    latest = 0
    for path in OFFICE_ASSET_PATHS:
        try:
            latest = max(latest, int(path.stat().st_mtime_ns))
        except OSError:
            continue
    return str(latest or int(datetime.now(timezone.utc).timestamp()))


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def status_path(job_id: str) -> Path:
    return job_dir(job_id) / "status.json"


def assets_path(job_id: str) -> Path:
    return job_dir(job_id) / "assets.json"


def excel_path(job_id: str) -> Path:
    return job_dir(job_id) / "cost_table.xlsx"


def excel_payload_path(job_id: str) -> Path:
    return job_dir(job_id) / "cost_table_payload.json"


def excel_audit_path(job_id: str) -> Path:
    return job_dir(job_id) / "cost_table_audit.json"


def asset_manifest_path(job_id: str) -> Path:
    return job_dir(job_id) / "asset_manifest.json"


def write_status(record: JobRecord) -> None:
    path = status_path(record.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    jobs[record.job_id] = record
    try:
        upsert_job(record)
    except Exception:
        logger.exception("quote database job sync failed job_id=%s", record.job_id)


def read_status(job_id: str) -> JobRecord:
    if job_id in jobs:
        return jobs[job_id]

    path = status_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found.")

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = JobRecord(**payload)
    jobs[job_id] = record
    return record


def write_assets(record: JobRecord, assets: list[dict]) -> None:
    path = assets_path(record.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    record.assets_path = str(path)
    record.updated_at = now_iso()
    write_status(record)
    try:
        replace_job_assets(record.job_id, assets)
    except Exception:
        logger.exception("quote database asset sync failed job_id=%s", record.job_id)


def read_assets_payload(job_id: str) -> list[dict]:
    record = read_status(job_id)
    path = Path(record.assets_path) if record.assets_path else assets_path(job_id)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def display_upload_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    return re.sub(r"(?<!\d)\d{2}-([^/\\]+)", r"\1", value)


def public_assets(job_id: str, assets: list[dict]) -> list[dict]:
    public: list[dict] = []
    seen: set[tuple] = set()
    for index, asset in enumerate(assets):
        kind = asset.get("kind")
        asset_type = asset.get("type")
        if kind not in {"source", "rendered_page"}:
            continue
        if kind == "rendered_page" and asset_type != "image":
            continue

        dedupe_key = (
            str(asset.get("path") or ""),
            str(kind or ""),
            str(asset_type or ""),
            str(asset.get("source") or ""),
            str(asset.get("page") or ""),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        public.append(
            {
                "id": index,
                "kind": kind,
                "type": asset_type,
                "label": display_upload_name(asset.get("label")),
                "source": display_upload_name(asset.get("source")),
                "page": asset.get("page"),
                "mime_type": asset.get("mime_type"),
                "url": f"/api/jobs/{job_id}/assets/{index}",
            }
        )
    return public


def public_job_payload(record: JobRecord) -> dict:
    payload = asdict(record)
    payload["status_url"] = f"/api/jobs/{record.job_id}"
    payload["report_url"] = f"/api/jobs/{record.job_id}/report"
    payload["assets_url"] = f"/api/jobs/{record.job_id}/assets"
    payload["feedback_url"] = f"/api/jobs/{record.job_id}/feedback"
    audit_file = excel_audit_path(record.job_id)
    if audit_file.exists():
        try:
            audit_payload = json.loads(audit_file.read_text(encoding="utf-8"))
            if isinstance(audit_payload, dict):
                payload["excel_audit"] = {
                    "verdict": audit_payload.get("verdict"),
                    "quality_level": audit_payload.get("quality_level"),
                    "confidence": audit_payload.get("confidence"),
                    "issues": audit_payload.get("issues") or [],
                    "missing_fields": audit_payload.get("missing_fields") or [],
                    "warnings": audit_payload.get("warnings") or [],
                }
        except Exception:
            logger.exception("quote excel audit payload read failed job_id=%s", record.job_id)
    if excel_path(record.job_id).exists():
        payload["excel_url"] = f"/api/jobs/{record.job_id}/excel"
    if db_enabled():
        deleted_at = job_admin_deleted_at(record.job_id)
        if deleted_at:
            payload["admin_deleted_at"] = deleted_at
    return payload


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def text_preview(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def read_text_preview(path_value: str | None, max_chars: int) -> str:
    if not path_value:
        return ""
    try:
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            return ""
        return text_preview(path.read_text(encoding="utf-8", errors="replace"), max_chars)
    except Exception:
        logger.exception("quote feedback rag text read failed path=%s", path_value)
        return ""


def read_json_payload(path: Path) -> object | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("quote feedback rag json read failed path=%s", path)
        return None


def json_preview(value: object, max_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value)
    return text_preview(text, max_chars)


def uploaded_file_names_from_disk(job_id: str) -> list[str]:
    input_dir = job_dir(job_id) / "input"
    if not input_dir.exists():
        return []
    names: list[str] = []
    for path in sorted(item for item in input_dir.iterdir() if item.is_file()):
        names.append(stored_upload_original_name(path))
    return names


def excel_row_summary(payload: object, max_rows: int = 40) -> list[str]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []

    preferred_keys = (
        "no",
        "item",
        "name",
        "part_name",
        "description",
        "material",
        "qty",
        "quantity",
        "process",
        "surface_treatment",
        "weight",
        "amount",
    )
    lines: list[str] = []
    for index, row in enumerate(rows[:max_rows], start=1):
        if not isinstance(row, dict):
            continue
        parts: list[str] = []
        for key in preferred_keys:
            value = row.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={text_preview(value, 80)}")
        if not parts:
            parts.append(text_preview(row, 180))
        lines.append(f"{index}. " + "; ".join(parts))
    return lines


def build_feedback_rag_document(record: JobRecord, auth: AuthContext, feedback: dict) -> tuple[str, str, dict]:
    verdict = str(feedback.get("verdict") or "").strip().lower()
    verdict_label = "qualified" if verdict == "qualified" else "unqualified"
    note = str(feedback.get("note") or "").strip()
    report_max = max(_env_int("QUOTE_RAG_FEEDBACK_REPORT_MAX_CHARS", 3200), 400)
    json_max = max(_env_int("QUOTE_RAG_FEEDBACK_JSON_MAX_CHARS", 3200), 400)

    file_names = uploaded_file_names_from_disk(record.job_id)
    excel_audit = read_json_payload(excel_audit_path(record.job_id))
    excel_payload = read_json_payload(excel_payload_path(record.job_id))
    row_lines = excel_row_summary(excel_payload, max_rows=max(_env_int("QUOTE_RAG_FEEDBACK_MAX_ROWS", 40), 1))
    report_excerpt = read_text_preview(record.report_path, report_max)
    review_excerpt = read_text_preview(record.review_path, 1600)

    reusable_guidance = (
        "This is a positive example. Similar drawing recognition, BOM decomposition, row-image matching, "
        "and Excel output patterns can be used as reference evidence, but prices and quantities must still "
        "be recalculated from the current drawing."
        if verdict == "qualified"
        else
        "This is a negative example. For similar drawings, prioritize the user's note, audit issues, "
        "missing rows, wrong row-image crops, or incomplete Excel decomposition before generating final output."
    )

    lines = [
        "# Quote Agent user feedback case",
        f"- Job ID: {record.job_id}",
        f"- Feedback verdict: {verdict_label}",
        f"- User: {auth.username} ({auth.role})",
        f"- Job status: {record.status}",
        f"- Review status: {record.review_status or ''}",
        f"- Uploaded files: {', '.join(file_names) if file_names else 'not recorded'}",
        f"- Prompt: {text_preview(record.prompt, 1200)}",
        f"- User note: {text_preview(note, 2000) if note else 'none'}",
        f"- Reusable guidance: {reusable_guidance}",
    ]

    if row_lines:
        lines.append("\n## Excel row summary")
        lines.extend(row_lines)

    if isinstance(excel_audit, dict):
        audit_summary = {
            "verdict": excel_audit.get("verdict"),
            "quality_level": excel_audit.get("quality_level"),
            "confidence": excel_audit.get("confidence"),
            "issues": excel_audit.get("issues") or [],
            "missing_fields": excel_audit.get("missing_fields") or [],
            "warnings": excel_audit.get("warnings") or [],
        }
        lines.append("\n## Excel audit summary")
        lines.append(json_preview(audit_summary, json_max))

    if report_excerpt:
        lines.append("\n## Final report excerpt")
        lines.append(report_excerpt)

    if review_excerpt:
        lines.append("\n## Review excerpt")
        lines.append(review_excerpt)

    metadata = {
        "job_id": record.job_id,
        "user_id": auth.user_id,
        "username": auth.username,
        "role": auth.role,
        "verdict": verdict_label,
        "file_names": file_names,
        "status": record.status,
        "review_status": record.review_status,
        "has_excel": excel_path(record.job_id).exists(),
        "has_excel_payload": excel_payload is not None,
        "has_excel_audit": excel_audit is not None,
        "prompt_preview": text_preview(record.prompt, 300),
    }
    title = f"Feedback case {record.job_id[:8]} {verdict_label}"
    return title, "\n".join(lines), metadata


async def sync_feedback_to_rag_safely(record: JobRecord, auth: AuthContext, feedback: dict) -> dict:
    try:
        title, content, metadata = build_feedback_rag_document(record, auth, feedback)
        result = await sync_feedback_document_to_rag(
            job_id=record.job_id,
            feedback=feedback,
            title=title,
            content=content,
            metadata=metadata,
        )
        if result.get("synced"):
            logger.info("quote feedback rag synced job_id=%s result=%s", record.job_id, result)
        else:
            logger.info("quote feedback rag skipped job_id=%s result=%s", record.job_id, result)
        return result
    except Exception as exc:
        logger.exception("quote feedback rag sync failed job_id=%s", record.job_id)
        return {"synced": False, "error": str(exc)}


def resolve_asset_path(job_id: str, asset: dict) -> Path:
    raw_path = asset.get("path")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Asset path is missing.")

    path = Path(str(raw_path)).resolve()
    root = job_dir(job_id).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Asset path is outside the job directory.") from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset file not found.")
    return path


def require_uploaded_files(uploads: list[UploadFile] | None) -> list[UploadFile]:
    files = [upload for upload in uploads or [] if upload.filename]
    if not files:
        raise HTTPException(status_code=400, detail="请先上传图纸 / PDF / 图片。")
    return files


def stored_upload_original_name(path: Path) -> str:
    match = re.match(r"^\d{2}-(.+)$", path.name)
    return match.group(1) if match else path.name


def guess_mime_type(path: Path, fallback: str = "") -> str:
    return fallback or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def saved_upload_paths(uploads: list[SavedUpload]) -> list[Path]:
    return [item.path for item in uploads]


def saved_upload_file_names(uploads: list[SavedUpload]) -> list[str]:
    return [item.original_name for item in uploads]


def saved_upload_records(uploads: list[SavedUpload]) -> list[dict]:
    records: list[dict] = []
    for index, item in enumerate(uploads, start=1):
        records.append(
            {
                "file_index": index,
                "original_name": item.original_name,
                "stored_name": item.stored_name,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "page_count": item.page_count,
            }
        )
    return records


def file_record_from_path(path: Path, index: int) -> dict:
    data = path.read_bytes()
    mime_type = guess_mime_type(path)
    page_count = None
    if path.suffix.lower() == ".pdf":
        page_dir = path.parent / "_pdf_pages" / path.stem
        if page_dir.exists():
            page_count = len(list(page_dir.glob("page-*.png"))) or None
    return {
        "file_index": index,
        "original_name": stored_upload_original_name(path),
        "stored_name": path.name,
        "mime_type": mime_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "page_count": page_count,
    }


def backfill_job_files_from_disk() -> None:
    if not db_enabled() or not JOBS_DIR.exists():
        return

    for job_path in JOBS_DIR.iterdir():
        if not job_path.is_dir():
            continue

        input_dir = job_path / "input"
        if not input_dir.exists():
            continue

        records: list[dict] = []
        for index, path in enumerate(sorted(item for item in input_dir.iterdir() if item.is_file()), start=1):
            try:
                records.append(file_record_from_path(path, index))
            except Exception:
                logger.exception("quote file metadata backfill failed job_id=%s path=%s", job_path.name, path)

        if records:
            try:
                replace_job_files(job_path.name, records)
            except Exception:
                logger.exception("quote job file backfill failed job_id=%s", job_path.name)


async def save_uploads(job_id: str, uploads: list[UploadFile] | None) -> list[SavedUpload]:
    input_dir = job_dir(job_id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SavedUpload] = []

    for index, upload in enumerate(uploads or []):
        if not upload.filename:
            continue

        destination = input_dir / f"{index + 1:02d}-{safe_name(upload.filename)}"
        data = await upload.read()
        destination.write_bytes(data)
        saved.append(
            SavedUpload(
                path=destination,
                original_name=Path(upload.filename).name,
                stored_name=destination.name,
                mime_type=guess_mime_type(destination, upload.content_type or ""),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )

    return saved


def save_downloaded_files(job_id: str, downloads: list[FeishuDownloadedFile]) -> list[SavedUpload]:
    input_dir = job_dir(job_id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SavedUpload] = []

    for index, item in enumerate(downloads):
        if not item.file_name or not item.content:
            continue
        original_name = Path(item.file_name).name
        destination = input_dir / f"{index + 1:02d}-{safe_name(original_name)}"
        destination.write_bytes(item.content)
        saved.append(
            SavedUpload(
                path=destination,
                original_name=original_name,
                stored_name=destination.name,
                mime_type=guess_mime_type(destination, item.mime_type),
                size_bytes=len(item.content),
                sha256=hashlib.sha256(item.content).hexdigest(),
            )
        )

    return saved


async def generate_report(
    prompt: str,
    files: list[Path],
    max_review_rounds: int,
    audit: bool,
    work_model: str | None,
    vision_model: str | None,
    review_model: str | None,
) -> str:
    models = build_model_config(
        work_model_override=work_model,
        vision_model_override=vision_model,
        review_model_override=review_model,
    )
    logger.info(
        "quote generate start job_files=%s vision=%s work=%s review=%s audit=%s rounds=%s",
        len(files),
        models.vision_model_label,
        models.work_model_label,
        models.review_model_label,
        audit,
        max_review_rounds,
    )
    return await run_quote(
        prompt=prompt,
        files=files,
        models=models,
        max_review_rounds=max_review_rounds,
        include_audit=audit,
    )


def render_draft_report(report: str) -> str:
    return f"""# 初版报价报告

> 自动审核正在后台进行。以下是系统生成的第一版，先给你查看；正式使用前请结合后续审核结果复核。

{report}
"""


def review_field(review: object, name: str, default: object = "") -> object:
    if isinstance(review, dict):
        return review.get(name, default)
    return getattr(review, name, default)


def review_passed(review: object) -> bool:
    if isinstance(review, dict):
        return str(review.get("verdict", "")).lower() == "pass"
    return bool(getattr(review, "passed", False))


def render_review_report(review: object) -> str:
    verdict = review_field(review, "verdict", "unknown")
    confidence = review_field(review, "confidence", "unknown")
    issues = review_field(review, "issues", []) or []
    revision_prompt = review_field(review, "revision_prompt", "") or ""

    conclusion = "通过" if str(verdict).lower() == "pass" else "未通过"
    issue_lines = "\n".join(f"- {issue}" for issue in issues) or "- 无"
    return f"""# 自动审核结果

- 审核结论：{conclusion}
- 置信等级：{confidence}

## 审核问题

{issue_lines}

## 修正建议

{revision_prompt or "无"}
"""


async def generate_initial_report(
    prompt: str,
    files: list[Path],
    work_model: str | None,
    vision_model: str | None,
    review_model: str | None,
) -> tuple[str, str, object]:
    models = build_model_config(
        work_model_override=work_model,
        vision_model_override=vision_model,
        review_model_override=review_model,
    )
    logger.info(
        "quote draft start job_files=%s vision=%s work=%s review=%s",
        len(files),
        models.vision_model_label,
        models.work_model_label,
        models.review_model_label,
    )
    vision_context = await extract_vision_context(prompt, files, models.vision_model, models.vision_model_label)
    if files and not vision_context.strip():
        raise RuntimeError("图纸识别摘要为空，未生成初版报价。请重试或检查 vision 上游模型。")

    working_prompt = build_prompt_with_vision_context(prompt, vision_context) if vision_context else prompt
    draft = await generate_once(working_prompt, [], models.work_model, models.work_model_label)
    return draft, working_prompt, models


async def run_job_langgraph(
    record: JobRecord,
    files: list[Path],
    max_review_rounds: int,
    audit: bool,
    work_model: str | None,
    vision_model: str | None,
    review_model: str | None,
) -> None:
    record.status = "running"
    record.review_status = "pending"
    record.updated_at = now_iso()
    write_status(record)
    event_token = bind_office_event_context(record.job_id, job_dir(record.job_id))
    log_office_event(
        "quote_job",
        "job_started",
        status="running",
        message="报价任务开始执行。",
        metadata={"engine": "langgraph", "files": len(files), "max_review_rounds": max_review_rounds, "audit": audit},
    )

    report_path = job_dir(record.job_id) / "report.md"
    audit_path = job_dir(record.job_id) / "review.md"
    draft_written = False
    assets_written = False
    manifest_written = False

    try:
        from .langgraph_workflow import build_quote_graph

        models = build_model_config(
            work_model_override=work_model,
            vision_model_override=vision_model,
            review_model_override=review_model,
        )
        logger.info(
            "quote langgraph start job_id=%s job_files=%s vision=%s work=%s review=%s rounds=%s",
            record.job_id,
            len(files),
            models.vision_model_label,
            models.work_model_label,
            models.review_model_label,
            max_review_rounds,
        )

        graph = build_quote_graph()
        graph_input = {
            "prompt": record.prompt,
            "files": files,
            "vision_model": models.vision_model,
            "vision_model_name": models.vision_model_label,
            "work_model": models.work_model,
            "work_model_name": models.work_model_label,
            "review_model": models.review_model,
            "review_model_name": models.review_model_label,
            "max_review_rounds": max_review_rounds,
            "include_audit": audit,
            "audit_log": [],
            "revision_count": 0,
        }

        final_state = {}
        async for state in graph.astream(graph_input, stream_mode="values"):
            final_state = state
            assets = list(state.get("assets") or [])
            if assets and not assets_written:
                write_assets(record, assets)
                assets_written = True
                logger.info(
                    "quote langgraph assets ready job_id=%s assets=%s",
                    record.job_id,
                    len(assets),
                )

            asset_manifest = state.get("asset_manifest")
            if asset_manifest and not manifest_written:
                manifest_path = asset_manifest_path(record.job_id)
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(json.dumps(asset_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest_written = True
                logger.info(
                    "quote asset manifest ready job_id=%s profile=%s assets=%s",
                    record.job_id,
                    (asset_manifest.get("summary") or {}).get("input_profile") if isinstance(asset_manifest, dict) else "",
                    len((asset_manifest.get("assets") or []) if isinstance(asset_manifest, dict) else []),
                )

            candidate_report = str(state.get("candidate_report") or "")
            if candidate_report.strip() and not draft_written:
                report_path.write_text(render_draft_report(candidate_report), encoding="utf-8")
                draft_written = True
                record.status = "draft_ready"
                record.report_path = str(report_path)
                record.review_status = "running"
                record.updated_at = now_iso()
                write_status(record)
                logger.info(
                    "quote langgraph draft ready job_id=%s chars=%s",
                    record.job_id,
                    len(candidate_report),
                )
                log_office_event(
                    "quote_job",
                    "draft_ready",
                    status="running",
                    message="报价初版已生成，进入审核/输出阶段。",
                    metadata={"chars": len(candidate_report), "review_status": record.review_status},
                )

        final_report = str(final_state.get("final_report") or final_state.get("candidate_report") or "")
        if not final_report.strip():
            raise RuntimeError("LangGraph workflow finished without a final report.")

        audit_log = final_state.get("audit_log") or []
        review_text = ""
        if audit_log:
            latest_review = audit_log[-1]
            review_text = render_review_report(latest_review)
            audit_path.write_text(review_text, encoding="utf-8")
            record.review_status = "passed" if review_passed(latest_review) else "failed"
            record.review_path = str(audit_path)
        else:
            record.review_status = "skipped"

        if review_text:
            final_report = f"{final_report}\n\n---\n\n{review_text}"

        excel_note = ""
        excel_enabled = os.getenv("QUOTE_EXCEL_OUTPUT_ENABLED", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        if excel_enabled:
            try:
                from .excel_agent import generate_sheet_metal_excel

                excel_result = await generate_sheet_metal_excel(
                    user_prompt=record.prompt,
                    vision_context=str(final_state.get("vision_context") or ""),
                    final_report=final_report,
                    work_model=models.work_model,
                    work_model_name=models.work_model_label,
                    output_path=excel_path(record.job_id),
                    payload_path=excel_payload_path(record.job_id),
                    image_assets=list(final_state.get("assets") or []),
                    asset_manifest=final_state.get("asset_manifest") if isinstance(final_state.get("asset_manifest"), dict) else None,
                    vision_files=files,
                    vision_model_name=models.vision_model_label,
                    review_model=models.review_model,
                    review_model_name=models.review_model_label,
                )
                if excel_result:
                    audit = excel_result.audit
                    audit_lines = ""
                    if audit:
                        verdict_label = {
                            "pass": "通过，可作为正式成本表输出",
                            "needs_confirmation": "待确认版，真实但不能作为正式总价",
                            "fail": "未通过，未开放正式下载",
                        }.get(audit.verdict, audit.verdict)
                        issue_lines = "\n".join(f"- {item}" for item in audit.issues[:8])
                        missing_lines = "\n".join(f"- {item}" for item in audit.missing_fields[:12])
                        audit_lines = (
                            "\n\nExcel 审核：\n"
                            f"- 结论：{verdict_label}\n"
                            f"- 等级：{audit.quality_level}\n"
                            f"- 置信度：{audit.confidence}\n"
                            f"- 审核轮次：{audit.attempts}"
                        )
                        if issue_lines:
                            audit_lines += f"\n\n审核问题：\n{issue_lines}"
                        if missing_lines:
                            audit_lines += f"\n\n缺失/待确认字段：\n{missing_lines}"
                    warning_lines = "\n".join(
                        f"- {item}" for item in [*excel_result.workbook.warnings, *((excel_result.audit.warnings if excel_result.audit else []) or [])]
                    )
                    warning_block = f"\n\n待复核：\n{warning_lines}" if warning_lines else ""
                    if excel_result.skipped:
                        download_line = "- Excel 审核未通过，未开放正式下载。"
                    elif excel_result.audit and excel_result.audit.verdict == "needs_confirmation":
                        download_line = f"- [下载待确认 Excel 成本拆解表](/api/jobs/{record.job_id}/excel)"
                    else:
                        download_line = f"- [下载 Excel 成本拆解表](/api/jobs/{record.job_id}/excel)"
                    excel_note = (
                        "\n\n---\n\n"
                        "## Excel 模板结果\n\n"
                        f"{download_line}\n"
                        f"- 已按模板写入 {excel_result.workbook.row_count} 行明细。"
                        f"{audit_lines}"
                        f"{warning_block}"
                    )
                    logger.info(
                        "quote excel workbook ready job_id=%s rows=%s path=%s",
                        record.job_id,
                        excel_result.workbook.row_count,
                        excel_result.workbook.path,
                    )
            except Exception as exc:
                logger.exception("quote excel workbook generation failed job_id=%s", record.job_id)
                excel_note = (
                    "\n\n---\n\n"
                    "## Excel 模板结果\n\n"
                    f"Excel 成本拆解表生成失败：{exc}"
                )

        if excel_note:
            final_report = f"{final_report}{excel_note}"
        report_path.write_text(final_report, encoding="utf-8")
        record.status = "completed"
        record.report_path = str(report_path)
        record.updated_at = now_iso()
        write_status(record)
        log_office_event(
            "quote_job",
            "job_completed",
            status="done",
            message="报价任务已完成。",
            metadata={"review_status": record.review_status, "report_chars": len(final_report)},
        )
        logger.info(
            "quote langgraph completed job_id=%s review_status=%s report_chars=%s",
            record.job_id,
            record.review_status,
            len(final_report),
        )
    except Exception as exc:
        if draft_written and report_path.exists():
            review_text = f"# 自动审核失败\n\n错误：{exc}\n"
            audit_path.write_text(review_text, encoding="utf-8")
            with report_path.open("a", encoding="utf-8") as report_file:
                report_file.write("\n\n---\n\n")
                report_file.write(review_text)
            record.status = "completed"
            record.report_path = str(report_path)
            record.review_status = "error"
            record.review_path = str(audit_path)
            record.review_error = str(exc)
        else:
            record.status = "failed"
            record.error = str(exc)
            record.review_status = "skipped"
        record.updated_at = now_iso()
        write_status(record)
        log_office_event(
            "quote_job",
            "job_failed" if record.status == "failed" else "job_completed_with_review_error",
            status="failed" if record.status == "failed" else "done",
            message="报价任务执行失败。" if record.status == "failed" else "报价任务已完成，但审核阶段异常。",
            error=str(exc),
            metadata={"review_status": record.review_status},
        )
    finally:
        reset_office_event_context(event_token)


async def run_job_langgraph_with_feishu_notification(
    record: JobRecord,
    files: list[Path],
    max_review_rounds: int,
    audit: bool,
    work_model: str | None,
    vision_model: str | None,
    review_model: str | None,
    receive_id: str,
    receive_id_type: str = "chat_id",
    base_url: str = "",
) -> None:
    try:
        await run_job_langgraph(record, files, max_review_rounds, audit, work_model, vision_model, review_model)
    finally:
        try:
            latest = read_status(record.job_id)
            report_text = ""
            if latest.report_path and Path(latest.report_path).exists():
                report_text = Path(latest.report_path).read_text(encoding="utf-8", errors="replace")
            if latest.status == "failed":
                text = quote_completed_text(
                    latest.job_id,
                    report_text,
                    "",
                    status=latest.status,
                    error=latest.error or latest.review_error or "",
                )
                await send_feishu_text(receive_id, text, receive_id_type=receive_id_type)
                return

            await send_feishu_report_messages(receive_id, latest.job_id, report_text, receive_id_type)
            workbook_path = excel_path(latest.job_id)
            if workbook_path.exists():
                try:
                    await send_feishu_file(receive_id, workbook_path, receive_id_type=receive_id_type)
                except Exception:
                    logger.exception("feishu excel file delivery failed job_id=%s", record.job_id)
                    fallback_base_url = base_url or os.getenv("QUOTE_PUBLIC_BASE_URL", "").strip() or ""
                    if fallback_base_url:
                        await send_feishu_text(
                            receive_id,
                            f"Excel 文件发送到飞书失败，可先用备用链接下载：{fallback_base_url.rstrip('/')}/jobs/{latest.job_id}/excel",
                            receive_id_type=receive_id_type,
                        )
                    else:
                        await send_feishu_text(
                            receive_id,
                            "Excel 文件发送到飞书失败，请联系管理员检查飞书文件上传权限。",
                            receive_id_type=receive_id_type,
                        )
            else:
                await send_feishu_text(
                    receive_id,
                    "本次任务未生成 Excel 文件，报告内容已发送在上方。",
                    receive_id_type=receive_id_type,
                )
        except Exception:
            logger.exception("feishu quote completion notification failed job_id=%s", record.job_id)


async def run_job(
    record: JobRecord,
    files: list[Path],
    max_review_rounds: int,
    audit: bool,
    work_model: str | None,
    vision_model: str | None,
    review_model: str | None,
) -> None:
    record.status = "running"
    record.review_status = "pending"
    record.updated_at = now_iso()
    write_status(record)
    event_token = bind_office_event_context(record.job_id, job_dir(record.job_id))
    log_office_event(
        "quote_job",
        "job_started",
        status="running",
        message="报价任务开始执行。",
        metadata={"engine": "fallback", "files": len(files), "max_review_rounds": max_review_rounds, "audit": audit},
    )

    try:
        report, review_prompt, models = await generate_initial_report(
            prompt=record.prompt,
            files=files,
            work_model=work_model,
            vision_model=vision_model,
            review_model=review_model,
        )
        report_path = job_dir(record.job_id) / "report.md"
        report_path.write_text(render_draft_report(report), encoding="utf-8")
        record.status = "draft_ready"
        record.report_path = str(report_path)
        record.review_status = "running" if max_review_rounds > 0 else "skipped"
        record.updated_at = now_iso()
        write_status(record)
        log_office_event(
            "quote_job",
            "draft_ready",
            status="running",
            message="报价初版已生成，进入审核阶段。",
            metadata={"chars": len(report), "review_status": record.review_status},
        )

        if max_review_rounds <= 0:
            record.status = "completed"
            record.updated_at = now_iso()
            write_status(record)
            log_office_event(
                "quote_job",
                "job_completed",
                status="done",
                message="报价任务已完成。",
                metadata={"review_status": record.review_status, "report_chars": len(report)},
            )
            return

        try:
            review = await review_once(
                review_prompt,
                report,
                [],
                models.review_model,
                models.review_model_label,
            )
            audit_path = job_dir(record.job_id) / "review.md"
            review_text = render_review_report(review)
            audit_path.write_text(review_text, encoding="utf-8")
            with report_path.open("a", encoding="utf-8") as report_file:
                report_file.write("\n\n---\n\n")
                report_file.write(review_text)
            record.review_status = "passed" if review.passed else "failed"
            record.review_path = str(audit_path)
        except Exception as review_exc:
            audit_path = job_dir(record.job_id) / "review.md"
            review_text = f"# 自动审核失败\n\n错误：{review_exc}\n"
            audit_path.write_text(review_text, encoding="utf-8")
            with report_path.open("a", encoding="utf-8") as report_file:
                report_file.write("\n\n---\n\n")
                report_file.write(review_text)
            record.review_status = "error"
            record.review_path = str(audit_path)
            record.review_error = str(review_exc)

        record.status = "completed"
        record.updated_at = now_iso()
        write_status(record)
        log_office_event(
            "quote_job",
            "job_completed",
            status="done",
            message="报价任务已完成。",
            metadata={"review_status": record.review_status, "report_chars": len(report)},
        )
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        record.review_status = "skipped"
        record.updated_at = now_iso()
        write_status(record)
        log_office_event(
            "quote_job",
            "job_failed",
            status="failed",
            message="报价任务执行失败。",
            error=str(exc),
            metadata={"review_status": record.review_status},
        )
    finally:
        reset_office_event_context(event_token)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/feishu/bot/status")
async def feishu_bot_status() -> dict:
    return feishu_bot_config_status()


async def handle_feishu_create_job(action: FeishuBotAction, base_url: str) -> None:
    try:
        downloads = await download_feishu_attachments(action.attachments or [])
        if not downloads:
            await send_feishu_text(action.receive_id, "没有找到可报价的图纸文件，请重新上传。", action.receive_id_type)
            return

        user_id = ensure_feishu_event_user(action.sender or {})
        job_id = uuid.uuid4().hex
        saved_uploads = save_downloaded_files(job_id, downloads)
        saved_files = saved_upload_paths(saved_uploads)
        record = JobRecord(
            job_id=job_id,
            status="queued",
            created_at=now_iso(),
            updated_at=now_iso(),
            prompt=action.prompt,
            user_id=user_id,
        )
        write_status(record)
        try:
            upsert_job(record, saved_upload_file_names(saved_uploads))
            replace_job_files(record.job_id, saved_upload_records(saved_uploads))
        except Exception:
            logger.exception("feishu quote database initial job sync failed job_id=%s", record.job_id)

        await send_feishu_text(action.receive_id, quote_created_text(job_id, record.created_at), action.receive_id_type)
        await run_job_langgraph_with_feishu_notification(
            record,
            saved_files,
            feishu_bot_max_review_rounds(),
            feishu_bot_audit_enabled(),
            None,
            None,
            None,
            action.receive_id,
            action.receive_id_type,
            base_url,
        )
    except Exception as exc:
        logger.exception("feishu quote job creation failed")
        try:
            await send_feishu_text(action.receive_id, f"创建报价任务失败：{exc}", action.receive_id_type)
        except Exception:
            logger.exception("feishu quote failure notification failed")


@app.post("/api/feishu/events")
async def feishu_events(request: Request) -> dict:
    if not feishu_bot_enabled():
        raise HTTPException(status_code=404, detail="Feishu bot is not enabled.")

    config_status = feishu_bot_config_status()
    if not config_status.get("event_security_configured"):
        raise HTTPException(status_code=503, detail="Feishu bot event security is not configured.")

    raw_body = await request.body()
    payload = decode_feishu_event(raw_body, dict(request.headers))
    challenge = feishu_challenge_response(payload)
    if challenge:
        return challenge

    if is_duplicate_feishu_event(payload):
        return {"ok": True, "duplicate": True}

    action = build_feishu_bot_action(payload)
    if action.kind == "reply":
        await send_feishu_text(action.receive_id, action.reply_text, action.receive_id_type)
    elif action.kind == "create_job":
        asyncio.create_task(handle_feishu_create_job(action, public_base_url(request)))
    return {"ok": True, "action": action.kind}


@app.get("/static/{file_path:path}")
async def static_file(file_path: str) -> FileResponse:
    if not file_path or "\0" in file_path:
        raise HTTPException(status_code=404, detail="Static file not found.")
    static_root = STATIC_DIR.resolve()
    path = (static_root / file_path).resolve()
    try:
        path.relative_to(static_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Static file not found.") from None
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Static file not found.")
    return FileResponse(path)


def login_page(error: str = "", next_url: str = "/") -> str:
    escaped_error = html.escape(error)
    safe_next = safe_next_url(next_url)
    escaped_next = html.escape(safe_next, quote=True)
    feishu_next_query = f"?next={quote(safe_next, safe='')}" if safe_next != "/" else ""
    error_block = f'<div class="error">{escaped_error}</div>' if escaped_error else ""
    feishu_block = ""
    if feishu_login_enabled():
        feishu_block = f"""
    <div class="login-divider"><span>或</span></div>
    <a class="feishu-button" href="/auth/feishu/start{feishu_next_query}">使用飞书登录</a>
"""
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>登录 - Quote Agent Assistant</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(60, 60, 67, .18);
      --glass: rgba(255, 255, 255, .76);
      --blue: #0071e3;
      --blue-hover: #0077ed;
      --shadow: 0 24px 70px rgba(31, 41, 55, .16);
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
      background:
        linear-gradient(105deg, rgba(247, 249, 252, .30) 0%, rgba(247, 249, 252, .52) 48%, rgba(247, 249, 252, .94) 100%),
        url("/static/login-bg.png") left center / cover no-repeat,
        #f5f7fb;
      color: var(--ink);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.42)),
        radial-gradient(circle at 76% 45%, rgba(255,255,255,.76), rgba(255,255,255,0) 40%);
    }}
    main {{
      position: relative;
      z-index: 1;
      min-height: 100vh;
      width: min(1180px, calc(100vw - 48px));
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(320px, 1fr) minmax(360px, 430px);
      align-items: center;
      gap: 72px;
      padding: 56px 0;
    }}
    .login-card {{
      width: 100%;
      justify-self: end;
      padding: 30px;
      border: 1px solid rgba(255, 255, 255, .72);
      border-radius: 24px;
      background: var(--glass);
      box-shadow: var(--shadow);
      backdrop-filter: blur(28px) saturate(150%);
      -webkit-backdrop-filter: blur(28px) saturate(150%);
    }}
    .brand-mark {{
      width: 44px;
      height: 44px;
      border-radius: 14px;
      display: grid;
      place-items: center;
      margin-bottom: 22px;
      color: #fff;
      font-weight: 800;
      letter-spacing: 0;
      background: linear-gradient(145deg, #1d1d1f, #4b5563);
      box-shadow: 0 12px 28px rgba(29, 29, 31, .20);
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.08;
      letter-spacing: 0;
      font-weight: 760;
    }}
    .subtitle {{
      margin: 10px 0 28px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.55;
    }}
    form {{
      display: grid;
      gap: 16px;
    }}
    .field {{
      display: grid;
      gap: 8px;
    }}
    label {{
      color: #2f3137;
      font-size: 13px;
      font-weight: 680;
    }}
    input {{
      width: 100%;
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: rgba(255, 255, 255, .84);
      color: var(--ink);
      font: inherit;
      outline: none;
      transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }}
    input:focus {{
      border-color: rgba(0, 113, 227, .58);
      background: rgba(255, 255, 255, .96);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, .14);
    }}
    button {{
      width: 100%;
      min-height: 48px;
      border: 0;
      border-radius: 14px;
      padding: 12px 18px;
      font: inherit;
      font-weight: 740;
      color: #fff;
      background: var(--blue);
      box-shadow: 0 14px 30px rgba(0, 113, 227, .24);
      cursor: pointer;
      transition: transform .16s ease, background .16s ease, box-shadow .16s ease;
    }}
    button:hover {{ background: var(--blue-hover); box-shadow: 0 16px 34px rgba(0, 113, 227, .28); }}
    button:active {{ transform: translateY(1px); }}
    .login-divider {{
      position: relative;
      display: grid;
      place-items: center;
      margin: 18px 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .login-divider::before {{
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 50%;
      height: 1px;
      background: rgba(60, 60, 67, .14);
    }}
    .login-divider span {{
      position: relative;
      z-index: 1;
      padding: 0 10px;
      background: rgba(255, 255, 255, .76);
    }}
    .feishu-button {{
      width: 100%;
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 14px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 18px;
      color: var(--ink);
      background: rgba(255, 255, 255, .84);
      font-weight: 740;
      text-decoration: none;
      box-shadow: 0 10px 24px rgba(31, 41, 55, .08);
      transition: transform .16s ease, background .16s ease, box-shadow .16s ease, border-color .16s ease;
    }}
    .feishu-button:hover {{
      border-color: rgba(0, 113, 227, .30);
      background: rgba(255, 255, 255, .96);
      box-shadow: 0 14px 30px rgba(31, 41, 55, .12);
      transform: translateY(-1px);
    }}
    .feishu-button:active {{ transform: translateY(0); }}
    .error {{
      border: 1px solid rgba(255, 69, 58, .24);
      color: #b42318;
      background: rgba(255, 241, 240, .86);
      border-radius: 14px;
      padding: 11px 12px;
      font-size: 14px;
      line-height: 1.45;
    }}
    .aside {{
      max-width: 560px;
      justify-self: start;
      padding: 24px 0;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 6px 12px;
      border: 1px solid rgba(60, 60, 67, .14);
      border-radius: 999px;
      background: rgba(255, 255, 255, .58);
      color: #3f4754;
      font-size: 13px;
      font-weight: 680;
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }}
    .aside h2 {{
      margin: 18px 0 12px;
      font-size: 48px;
      line-height: 1.04;
      letter-spacing: 0;
      font-weight: 780;
    }}
    .aside p {{
      margin: 0;
      max-width: 470px;
      color: #515b68;
      font-size: 17px;
      line-height: 1.65;
    }}
    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 26px;
    }}
    .meta-row span {{
      padding: 8px 11px;
      border: 1px solid rgba(60, 60, 67, .12);
      border-radius: 999px;
      background: rgba(255, 255, 255, .56);
      color: #414a56;
      font-size: 13px;
      font-weight: 650;
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
    }}
    @media (max-width: 860px) {{
      body {{
        background:
          linear-gradient(180deg, rgba(247, 249, 252, .88), rgba(247, 249, 252, .76)),
          url("/static/login-bg.png") center / cover no-repeat,
          #f5f7fb;
      }}
      main {{
        width: min(460px, calc(100vw - 32px));
        grid-template-columns: 1fr;
        gap: 28px;
        padding: 36px 0;
      }}
      .aside {{
        padding: 0;
      }}
      .aside h2 {{
        font-size: 36px;
      }}
      .aside p, .meta-row {{
        display: none;
      }}
      .login-card {{
        padding: 24px;
        border-radius: 22px;
      }}
    }}
  </style>
</head>
<body>
<main>
  <section class="aside" aria-label="Quote Agent Assistant">
    <div class="eyebrow">Quote Agent Assistant</div>
    <h2>成本报价工作台</h2>
    <p>图纸、识别、报价和审核记录，在一个安静清晰的工作界面里完成。</p>
    <div class="meta-row" aria-hidden="true">
      <span>Drawing</span>
      <span>Costing</span>
      <span>Review</span>
    </div>
  </section>
  <section class="login-card" aria-labelledby="login-title">
    <div class="brand-mark" aria-hidden="true">Q</div>
    <h1 id="login-title">欢迎回来</h1>
    <p class="subtitle">登录 Quote Agent Assistant，进入成本报价工作台。</p>
    <form action="/login" method="post">
      <input type="hidden" name="next" value="{escaped_next}" />
      {error_block}
      <div class="field">
        <label for="username">用户名</label>
        <input id="username" name="username" autocomplete="username" placeholder="请输入用户名" required />
      </div>
      <div class="field">
        <label for="password">密码</label>
        <input id="password" name="password" type="password" autocomplete="current-password" placeholder="请输入密码" required />
      </div>
      <button type="submit">登录</button>
    </form>
    {feishu_block}
  </section>
</main>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str | None = None):
    next_url = safe_next_url(next)
    if not db_enabled():
        return HTMLResponse(login_page("数据库登录尚未启用。", next_url))
    if current_auth(request):
        return RedirectResponse(next_url, status_code=303)
    return HTMLResponse(login_page(next_url=next_url))


@app.get("/auth/feishu/start")
async def feishu_start(request: Request, next: str | None = None):
    next_url = safe_next_url(next)
    if not db_enabled():
        return HTMLResponse(login_page("数据库登录尚未启用。", next_url), status_code=503)
    if not feishu_login_enabled():
        return HTMLResponse(login_page("飞书登录尚未启用。", next_url), status_code=503)

    state = secrets.token_urlsafe(24)
    response = RedirectResponse(
        build_feishu_authorize_url(feishu_redirect_uri(request), state),
        status_code=303,
    )
    response.set_cookie(
        FEISHU_STATE_COOKIE_NAME,
        state,
        max_age=10 * 60,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    response.set_cookie(
        FEISHU_NEXT_COOKIE_NAME,
        next_url,
        max_age=10 * 60,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    return response


@app.get("/auth/feishu/callback", name="feishu_callback")
async def feishu_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if not db_enabled():
        return HTMLResponse(login_page("数据库登录尚未启用。"), status_code=503)
    if not feishu_login_enabled():
        return HTMLResponse(login_page("飞书登录尚未启用。"), status_code=503)
    if error:
        return HTMLResponse(login_page("飞书授权未完成，请重新登录。"), status_code=400)

    saved_state = request.cookies.get(FEISHU_STATE_COOKIE_NAME) or ""
    if not code or not state or not saved_state or not secrets.compare_digest(saved_state, state):
        return HTMLResponse(login_page("飞书登录状态已失效，请重新登录。"), status_code=400)

    try:
        profile = await fetch_feishu_user_profile(code)
        provider_user_id = str(
            profile.get("union_id")
            or profile.get("open_id")
            or profile.get("user_id")
            or profile.get("sub")
            or ""
        ).strip()
        if not provider_user_id:
            raise FeishuAuthError("Feishu user identity is missing.")
        user = find_or_create_oauth_user(
            "feishu",
            provider_user_id,
            profile,
            default_role=feishu_default_role(),
        )
    except PermissionError:
        logger.warning("feishu login rejected because mapped user is disabled")
        return HTMLResponse(login_page("账号已被停用，请联系管理员。"), status_code=403)
    except Exception:
        logger.exception("feishu login failed")
        return HTMLResponse(login_page("飞书登录失败，请稍后重试或使用账号密码登录。"), status_code=502)

    client_host = request.client.host if request.client else ""
    session_token = create_session(
        int(user["id"]),
        user_agent=request.headers.get("user-agent", ""),
        ip_address=client_host,
    )
    next_url = safe_next_url(request.cookies.get(FEISHU_NEXT_COOKIE_NAME))
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=session_ttl_days() * 24 * 60 * 60,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    response.delete_cookie(FEISHU_STATE_COOKIE_NAME)
    response.delete_cookie(FEISHU_NEXT_COOKIE_NAME)
    return response


@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    next_url = safe_next_url(next)
    if not db_enabled():
        return HTMLResponse(login_page("数据库登录尚未启用。", next_url), status_code=503)

    user = authenticate_user(username.strip(), password)
    if not user:
        return HTMLResponse(login_page("用户名或密码错误。", next_url), status_code=401)

    client_host = request.client.host if request.client else ""
    session_token = create_session(
        int(user["id"]),
        user_agent=request.headers.get("user-agent", ""),
        ip_address=client_host,
    )
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=session_ttl_days() * 24 * 60 * 60,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    return response


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/office")
async def office_alias() -> RedirectResponse:
    return RedirectResponse("/agent-office", status_code=303)


@app.get("/jobs/{job_id}/report")
async def job_report_link(request: Request, job_id: str) -> RedirectResponse:
    if auth_required() and not current_auth(request):
        return login_redirect(f"/jobs/{job_id}/report")
    return RedirectResponse(f"/api/jobs/{job_id}/report", status_code=303)


@app.get("/jobs/{job_id}/excel")
async def job_excel_link(request: Request, job_id: str) -> RedirectResponse:
    if auth_required() and not current_auth(request):
        return login_redirect(f"/jobs/{job_id}/excel")
    return RedirectResponse(f"/api/jobs/{job_id}/excel", status_code=303)


@app.get("/agent-office", response_class=HTMLResponse)
async def agent_office(request: Request):
    if auth_required() and not current_auth(request):
        return login_redirect("/agent-office")
    if not OFFICE_PAGE_PATH.exists() or not OFFICE_PAGE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Agent office page not found.")
    asset_version = office_asset_version()
    html_text = OFFICE_PAGE_PATH.read_text(encoding="utf-8").replace(
        "__OFFICE_ASSET_VERSION__",
        asset_version,
    )
    return HTMLResponse(html_text)


@app.get("/api/office/state")
async def get_office_state(request: Request) -> dict:
    auth = require_token(request)
    include_all = auth.role == "admin"
    jobs_payload = list_jobs_for_user(
        auth.user_id,
        include_all=include_all,
        limit=50 if include_all else 30,
    )
    events_by_job: dict[str, list[dict[str, Any]]] = {}
    for job in jobs_payload:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        try:
            events_by_job[job_id] = read_office_events(job_dir(job_id), limit=40)
        except Exception:
            logger.exception("failed to read office events job_id=%s", job_id)
            events_by_job[job_id] = []
    return build_office_state(
        jobs_payload,
        username=auth.username,
        is_admin=include_all,
        events_by_job=events_by_job,
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if auth_required() and not current_auth(request):
        return login_redirect("/")

    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quote Agent Assistant</title>
  <style>
    :root {
      --page: #f5f5f7;
      --surface: rgba(255, 255, 255, .86);
      --surface-solid: #ffffff;
      --line: rgba(60, 60, 67, .16);
      --line-strong: rgba(60, 60, 67, .28);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --muted-2: #8e8e93;
      --blue: #0071e3;
      --blue-soft: #e8f2ff;
      --green: #0a7a3f;
      --red: #c2352a;
      --shadow: 0 14px 40px rgba(0, 0, 0, .08);
      --shadow-soft: 0 6px 22px rgba(0, 0, 0, .055);
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif; margin: 0; background: var(--page); color: var(--text); accent-color: var(--blue); }
    .app-shell { min-height: 100vh; display: grid; grid-template-columns: minmax(310px, 370px) minmax(0, 1fr); }
    .sidebar { position: sticky; top: 0; height: 100vh; min-width: 0; display: flex; flex-direction: column; border-right: 1px solid var(--line); background: var(--surface); backdrop-filter: blur(24px) saturate(1.25); -webkit-backdrop-filter: blur(24px) saturate(1.25); }
    .sidebar-header { padding: 24px 22px 18px; border-bottom: 1px solid rgba(60, 60, 67, .10); }
    .brand-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .brand-row h1 { margin: 0; max-width: 190px; font-size: 24px; font-weight: 760; line-height: 1.08; color: var(--text); }
    .sidebar .hint { margin: 12px 0 0; }
    .workspace { min-width: 0; height: 100vh; overflow: auto; padding: 34px; }
    .workspace-inner { max-width: 1180px; margin: 0 auto; }
    .workspace-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
    .workspace-header h2 { margin: 0; font-size: 28px; font-weight: 760; line-height: 1.12; color: var(--text); }
    .workspace-header .hint { margin: 8px 0 0; }
    .quote-form { background: var(--surface-solid); border: 1px solid var(--line); border-radius: 8px; padding: 22px; display: grid; gap: 18px; box-shadow: var(--shadow-soft); }
    .form-section { display: grid; gap: 10px; }
    .section-heading { display: flex; align-items: end; justify-content: space-between; gap: 12px; }
    .section-title { margin: 0; font-size: 16px; font-weight: 720; color: var(--text); }
    .section-hint { margin: 4px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .logout-form { margin: 0; background: transparent; border: 0; border-radius: 0; padding: 0; display: block; }
    .logout-form button { background: rgba(118, 118, 128, .12); color: var(--text); padding: 7px 11px; font-size: 13px; white-space: nowrap; }
    label { font-weight: 650; color: var(--text); }
    textarea, input, select { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 8px; padding: 11px 12px; font: inherit; color: var(--text); background: rgba(255, 255, 255, .92); transition: border-color .16s ease, box-shadow .16s ease, background .16s ease; }
    select { appearance: none; -webkit-appearance: none; padding-right: 34px; background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%); background-position: calc(100% - 18px) 50%, calc(100% - 13px) 50%; background-size: 5px 5px, 5px 5px; background-repeat: no-repeat; }
    textarea:focus, input:focus, select:focus { outline: none; border-color: rgba(0, 113, 227, .55); box-shadow: 0 0 0 4px rgba(0, 113, 227, .14); background-color: #fff; }
    textarea { resize: vertical; min-height: 126px; }
    .file-input-native { position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; pointer-events: none; }
    .upload-panel { display: grid; gap: 10px; }
    .upload-zone { min-height: 146px; border: 1px dashed rgba(0, 113, 227, .36); border-radius: 8px; display: grid; place-items: center; gap: 8px; padding: 22px; text-align: center; background: linear-gradient(180deg, #fbfdff 0%, #f4f9ff 100%); cursor: pointer; transition: border-color .16s ease, background .16s ease, box-shadow .16s ease, transform .16s ease; }
    .upload-zone:hover, .upload-zone.dragging { border-color: rgba(0, 113, 227, .62); background: #f1f7ff; box-shadow: inset 0 0 0 1px rgba(0, 113, 227, .08), 0 8px 22px rgba(0, 113, 227, .08); transform: translateY(-1px); }
    .upload-zone.has-files { border-style: solid; border-color: rgba(10, 122, 63, .28); background: #f6fff9; }
    .upload-mark { width: 44px; height: 44px; border-radius: 8px; display: grid; place-items: center; background: #ffffff; color: var(--blue); font-size: 25px; font-weight: 760; box-shadow: inset 0 0 0 1px var(--line), 0 5px 18px rgba(0, 0, 0, .055); }
    .upload-title { display: block; font-size: 17px; font-weight: 720; color: var(--text); }
    .upload-hint { display: block; max-width: 440px; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .file-summary { display: grid; gap: 8px; }
    .file-summary[hidden] { display: none; }
    .file-pill { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid var(--line); border-radius: 8px; padding: 9px 11px; background: #fbfbfd; color: var(--text); }
    .file-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 650; }
    .file-size { flex: 0 0 auto; color: var(--muted); font-size: 12px; }
    .request-grid { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .request-field { min-width: 0; display: grid; gap: 7px; }
    .request-field label { font-size: 14px; font-weight: 680; }
    .request-field textarea { min-height: 92px; }
    .request-field.full { grid-column: 1 / -1; }
    .prompt-preview { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfbfd; color: var(--muted); font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
    .prompt-preview strong { display: block; margin-bottom: 6px; color: var(--text); font-size: 14px; }
    .prompt-hidden { display: none; }
    input[type="checkbox"] { width: 18px; height: 18px; padding: 0; vertical-align: middle; }
    button { width: fit-content; border: 0; border-radius: 8px; padding: 10px 16px; font-weight: 700; background: var(--blue); color: white; cursor: pointer; transition: transform .14s ease, box-shadow .14s ease, background .14s ease, opacity .14s ease; }
    button:hover { box-shadow: 0 6px 16px rgba(0, 113, 227, .18); transform: translateY(-1px); }
    button:active { transform: translateY(0); box-shadow: none; }
    .secondary-button { width: 100%; margin-top: 16px; background: #1d1d1f; }
    .office-link { width: 100%; min-height: 40px; margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; padding: 10px 14px; background: #ffffff; color: var(--text); font-size: 14px; font-weight: 740; text-decoration: none; box-shadow: var(--shadow-soft); transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease; }
    .office-link:hover { border-color: rgba(0, 113, 227, .28); box-shadow: 0 8px 22px rgba(0, 113, 227, .10); transform: translateY(-1px); }
    .office-link:active { transform: translateY(0); box-shadow: var(--shadow-soft); }
    button[disabled] { opacity: 1; cursor: not-allowed; box-shadow: none; transform: none; color: var(--muted); background: rgba(118, 118, 128, .14); }
    .row { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .settings-grid { display: grid; gap: 12px; grid-template-columns: minmax(180px, .8fr) minmax(220px, 1.2fr); }
    .setting-card { min-height: 68px; border: 1px solid var(--line); border-radius: 8px; padding: 12px; display: grid; align-content: center; gap: 8px; background: #fbfbfd; }
    .setting-card label, .setting-title { font-size: 14px; font-weight: 680; color: var(--text); }
    .setting-card input[type="number"] { max-width: 150px; background: #ffffff; }
    .switch-control { min-height: 68px; border: 1px solid var(--line); border-radius: 8px; padding: 12px; display: flex; align-items: center; justify-content: space-between; gap: 14px; background: #fbfbfd; cursor: pointer; }
    .switch-control input { position: absolute; opacity: 0; pointer-events: none; }
    .switch-text { min-width: 0; display: grid; gap: 3px; }
    .switch-hint { color: var(--muted); font-size: 12px; line-height: 1.35; }
    .switch { position: relative; flex: 0 0 auto; width: 45px; height: 26px; border-radius: 999px; background: rgba(118, 118, 128, .30); transition: background .16s ease; }
    .switch::after { content: ""; position: absolute; width: 22px; height: 22px; top: 2px; left: 2px; border-radius: 50%; background: #ffffff; box-shadow: 0 2px 7px rgba(0, 0, 0, .22); transition: transform .16s ease; }
    .switch-control input:checked + .switch { background: var(--blue); }
    .switch-control input:checked + .switch::after { transform: translateX(19px); }
    .submit-row { display: flex; align-items: center; justify-content: flex-end; padding-top: 2px; }
    .hint { color: var(--muted); font-size: 14px; line-height: 1.45; }
    .status { margin-top: 18px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .92); padding: 14px 16px; font-weight: 650; box-shadow: var(--shadow-soft); }
    .status.error { border-color: rgba(194, 53, 42, .28); color: var(--red); background: #fff4f2; }
    .result-actions { margin-top: 18px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .result-actions[hidden] { display: none; }
    .result-action { min-height: 38px; border-radius: 8px; padding: 9px 13px; display: inline-flex; align-items: center; gap: 8px; background: #1d1d1f; color: #fff; font-size: 14px; font-weight: 720; text-decoration: none; box-shadow: 0 8px 22px rgba(29, 29, 31, .14); }
    .result-action:hover { background: #000; }
    .report { margin-top: 18px; background: var(--surface-solid); border: 1px solid var(--line); border-radius: 8px; padding: 30px; line-height: 1.68; overflow-x: auto; box-shadow: var(--shadow); }
    .report[hidden] { display: none; }
    .report > :first-child { margin-top: 0; }
    .report > :last-child { margin-bottom: 0; }
    .report h1 { margin: 0 0 18px; padding-bottom: 12px; border-bottom: 1px solid var(--line); font-size: 28px; font-weight: 760; line-height: 1.2; color: var(--text); }
    .report h2 { margin: 26px 0 11px; font-size: 21px; font-weight: 730; line-height: 1.28; color: var(--text); }
    .report h3 { margin: 20px 0 8px; font-size: 17px; font-weight: 700; line-height: 1.35; color: var(--text); }
    .report h4, .report h5, .report h6 { margin: 14px 0 8px; font-size: 15px; line-height: 1.35; color: #3a3a3c; }
    .report p { margin: 8px 0 12px; }
    .report ul, .report ol { margin: 8px 0 14px; padding-left: 24px; }
    .report li { margin: 4px 0; }
    .report table { width: 100%; border-collapse: separate; border-spacing: 0; margin: 14px 0 20px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; font-size: 14px; }
    .report th, .report td { border: 0; border-bottom: 1px solid rgba(60, 60, 67, .12); padding: 9px 11px; vertical-align: top; text-align: left; }
    .report th + th, .report td + td { border-left: 1px solid rgba(60, 60, 67, .10); }
    .report tr:last-child td { border-bottom: 0; }
    .report th { background: #f7f7f8; font-weight: 700; color: var(--text); }
    .report tr:nth-child(even) td { background: #fbfbfd; }
    .report code { border: 1px solid rgba(60, 60, 67, .16); border-radius: 5px; background: #f7f7f8; padding: 1px 5px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .92em; }
    .report a { color: var(--blue); font-weight: 700; text-decoration: none; }
    .report a:hover { text-decoration: underline; }
    .report pre { margin: 12px 0 16px; overflow-x: auto; border-radius: 8px; background: #1d1d1f; color: #f5f5f7; padding: 14px 16px; }
    .report pre code { border: 0; background: transparent; padding: 0; color: inherit; }
    .report blockquote { margin: 12px 0 16px; border-left: 3px solid var(--blue); padding: 6px 0 6px 12px; color: #3a3a3c; background: #f8fbff; }
    .report hr { margin: 24px 0; border: 0; border-top: 1px solid var(--line); }
    .feedback-panel { margin-top: 18px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, .92); padding: 18px; display: grid; gap: 14px; box-shadow: var(--shadow-soft); }
    .feedback-panel[hidden] { display: none; }
    .feedback-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
    .feedback-copy { min-width: 0; display: grid; gap: 4px; }
    .feedback-copy h3 { margin: 0; font-size: 17px; font-weight: 730; color: var(--text); }
    .feedback-copy p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .feedback-buttons { flex: 0 0 auto; display: flex; gap: 8px; }
    .feedback-choice { min-width: 82px; border: 1px solid var(--line); background: rgba(118, 118, 128, .10); color: var(--text); box-shadow: none; }
    .feedback-choice:hover { box-shadow: none; transform: translateY(-1px); background: rgba(118, 118, 128, .15); }
    .feedback-choice.selected.qualified { border-color: rgba(10, 122, 63, .28); background: #ecfdf3; color: var(--green); }
    .feedback-choice.selected.unqualified { border-color: rgba(194, 53, 42, .28); background: #fff1ef; color: var(--red); }
    .feedback-note { min-height: 74px; resize: vertical; background: #fbfbfd; }
    .feedback-state { min-height: 18px; color: var(--muted); font-size: 13px; }
    .feedback-state.error { color: var(--red); }
    .history-panel { min-height: 0; display: flex; flex: 1; flex-direction: column; padding: 16px 14px 14px; }
    .history-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
    .history-heading h2 { margin: 0; font-size: 18px; font-weight: 730; color: var(--text); }
    .history-heading button { background: rgba(118, 118, 128, .12); color: var(--text); padding: 7px 10px; font-size: 13px; }
    .history-search { margin-bottom: 10px; }
    .history-search input { padding: 9px 10px; font-size: 14px; background: rgba(118, 118, 128, .10); border-color: transparent; }
    .history-list { min-height: 0; overflow: auto; display: grid; align-content: start; gap: 8px; padding-right: 2px; }
    .history-empty { color: var(--muted); font-size: 14px; padding: 8px 0; }
    .history-item { position: relative; width: 100%; display: grid; gap: 6px; text-align: left; border: 1px solid transparent; border-radius: 8px; padding: 11px 12px; background: rgba(118, 118, 128, .08); color: var(--text); cursor: pointer; transition: border-color .14s ease, background .14s ease, transform .14s ease, box-shadow .14s ease, opacity .14s ease; }
    .history-item:hover { background: rgba(255, 255, 255, .78); border-color: rgba(60, 60, 67, .14); box-shadow: 0 5px 16px rgba(0, 0, 0, .055); transform: translateY(-1px); }
    .history-item:focus-visible { outline: none; border-color: rgba(0, 113, 227, .55); box-shadow: 0 0 0 4px rgba(0, 113, 227, .14); }
    .history-item.active { border-color: rgba(0, 113, 227, .34); background: #ffffff; box-shadow: 0 8px 22px rgba(0, 113, 227, .10); }
    .history-title-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
    .history-title { font-weight: 690; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .history-meta, .history-files { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .history-actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
    .history-delete { min-width: 28px; height: 24px; border-radius: 8px; padding: 0 7px; display: inline-grid; place-items: center; background: transparent; color: var(--muted-2); font-size: 15px; line-height: 1; }
    .history-delete:hover { background: #fff0ee; color: var(--red); box-shadow: none; transform: none; }
    .history-item.admin-deleted { border-color: rgba(194, 53, 42, .24); background: #fff8f7; color: rgba(29, 29, 31, .62); overflow: hidden; }
    .history-item.admin-deleted::before { content: ""; position: absolute; left: -12%; top: 50%; width: 124%; height: 2px; background: rgba(194, 53, 42, .72); transform: rotate(13deg); pointer-events: none; }
    .history-item.admin-deleted::after { content: "管理员已删除"; position: absolute; inset: 0; display: grid; place-items: center; color: rgba(194, 53, 42, .82); font-weight: 760; background: rgba(255, 248, 247, .56); pointer-events: none; }
    .history-item.admin-deleted > * { opacity: .34; }
    .history-item.admin-deleted .history-actions { position: relative; z-index: 2; opacity: 1; }
    .history-item.admin-deleted .history-delete { background: rgba(255, 255, 255, .84); color: var(--red); }
    .history-footer { flex: 0 0 auto; display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid rgba(60, 60, 67, .10); margin-top: 10px; }
    .history-summary { color: var(--muted); font-size: 12px; text-align: center; min-height: 17px; }
    .pagination { display: flex; align-items: center; justify-content: center; gap: 5px; flex-wrap: wrap; }
    .page-button { min-width: 28px; height: 28px; border-radius: 8px; padding: 0 8px; display: inline-grid; place-items: center; background: rgba(118, 118, 128, .10); color: var(--text); font-size: 12px; font-weight: 700; }
    .page-button:hover { box-shadow: none; transform: none; background: rgba(118, 118, 128, .16); }
    .page-button.active { background: var(--text); color: white; }
    .page-button:disabled { color: var(--muted-2); background: rgba(118, 118, 128, .08); }
    .page-ellipsis { color: var(--muted-2); font-size: 12px; padding: 0 2px; }
    .status-badge { flex: 0 0 auto; border-radius: 999px; padding: 2px 7px; font-size: 11px; line-height: 1.45; color: #5b5b60; background: rgba(118, 118, 128, .14); }
    .status-badge.completed { color: var(--green); background: #e8f7ef; }
    .status-badge.running, .status-badge.draft_ready, .status-badge.queued { color: #075ca8; background: var(--blue-soft); }
    .status-badge.failed { color: var(--red); background: #fff0ee; }
    .assets { margin-top: 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px; }
    .assets[hidden] { display: none; }
    .asset { margin: 0; background: #ffffff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow-soft); }
    .asset img { width: 100%; aspect-ratio: 4 / 3; object-fit: contain; display: block; background: #f7f7f8; }
    .asset-preview { width: 100%; border: 0; border-radius: 0; padding: 0; display: block; background: transparent; color: inherit; cursor: zoom-in; }
    .asset-preview:hover { box-shadow: none; transform: none; }
    .asset-preview img { transition: transform .16s ease; }
    .asset.image-card:hover .asset-preview img { transform: scale(1.02); }
    .asset a { color: inherit; text-decoration: none; }
    .asset > a:last-child { display: block; padding: 9px 11px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; background: #fff; }
    .assets-more { min-height: 128px; border: 1px dashed rgba(0, 113, 227, .34); border-radius: 8px; display: grid; place-items: center; background: #f7fbff; color: var(--blue); font-weight: 760; }
    .assets-more:hover { box-shadow: none; transform: none; background: #eef6ff; }
    .viewer { position: fixed; inset: 0; z-index: 80; display: grid; grid-template-rows: auto 1fr; background: rgba(20, 20, 22, .92); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); }
    .viewer[hidden] { display: none; }
    .viewer-toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; color: white; background: rgba(29, 29, 31, .82); border-bottom: 1px solid rgba(255, 255, 255, .12); }
    .viewer-title { flex: 1; min-width: 0; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .viewer-toolbar button, .viewer-toolbar a { min-width: 36px; height: 34px; border: 1px solid rgba(255,255,255,.20); border-radius: 8px; padding: 0 10px; display: inline-grid; place-items: center; background: rgba(255,255,255,.12); color: white; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
    .viewer-toolbar button:hover, .viewer-toolbar a:hover { background: rgba(255,255,255,.22); box-shadow: none; transform: none; }
    .viewer-stage { overflow: auto; display: grid; place-items: start center; padding: 16px; }
    .viewer-stage img { display: block; max-width: none; background: white; box-shadow: 0 18px 60px rgba(0,0,0,.38); }
    .dialog { position: fixed; inset: 0; z-index: 90; display: grid; place-items: center; padding: 20px; background: rgba(29, 29, 31, .34); backdrop-filter: blur(18px) saturate(1.2); -webkit-backdrop-filter: blur(18px) saturate(1.2); }
    .dialog[hidden] { display: none; }
    .dialog-card { width: min(390px, 100%); border: 1px solid rgba(255, 255, 255, .68); border-radius: 8px; padding: 20px; background: rgba(255, 255, 255, .92); box-shadow: 0 18px 60px rgba(0, 0, 0, .20); }
    .dialog-card h2 { margin: 0; font-size: 19px; line-height: 1.25; color: var(--text); }
    .dialog-card p { margin: 10px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
    .dialog-actions button { padding: 9px 14px; }
    .dialog-cancel { background: rgba(118, 118, 128, .12); color: var(--text); }
    .danger-button { background: var(--red); }
    .danger-button:hover { box-shadow: 0 6px 16px rgba(194, 53, 42, .18); }
    @media (max-width: 900px) {
      .app-shell { display: block; }
      .sidebar { position: static; height: auto; max-height: none; border-right: 0; border-bottom: 1px solid var(--line); }
      .history-panel { max-height: 44vh; }
      .workspace { height: auto; overflow: visible; padding: 20px; }
      .workspace-header { display: block; }
      .brand-row h1 { max-width: none; font-size: 22px; }
      .quote-form, .report { padding: 18px; }
      .request-grid { grid-template-columns: 1fr; }
      .settings-grid { grid-template-columns: 1fr; }
      .submit-row { justify-content: stretch; }
      .submit-row button { width: 100%; }
    }
  </style>
</head>
<body>
<main class="app-shell">
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="brand-row">
        <h1>Quote Agent Assistant</h1>
        <form class="logout-form" action="/logout" method="post"><button type="submit">退出登录</button></form>
      </div>
      <p class="hint">上传图纸或输入规格，系统会自动识别品类、生成报价，并经过审核 Agent 复核。</p>
      <button id="new-quote" class="secondary-button" type="button">新建成本报价</button>
      <a class="office-link" href="/agent-office">Agent 办公室</a>
    </div>
    <section class="history-panel" aria-labelledby="history-title">
      <div class="history-heading">
        <h2 id="history-title">报价记录</h2>
        <button id="refresh-history" type="button">刷新</button>
      </div>
      <div class="history-search">
        <input id="history-search" type="search" placeholder="搜索文件名、任务、用户" autocomplete="off" />
      </div>
      <div id="quote-history" class="history-list">
        <div class="history-empty">暂无报价记录</div>
      </div>
      <div class="history-footer">
        <div id="history-pagination" class="pagination" aria-label="报价记录分页"></div>
        <div id="history-summary" class="history-summary"></div>
      </div>
    </section>
  </aside>
  <section class="workspace">
    <div class="workspace-inner">
      <header class="workspace-header">
        <div>
          <h2>新建成本报价</h2>
          <p class="hint">选择图纸、PDF 或图片后提交，结果会保存在左侧报价记录中。</p>
        </div>
      </header>
      <form id="quote-form" class="quote-form" action="/ui/quote" method="post" enctype="multipart/form-data">
        <section class="form-section">
          <div class="section-heading">
            <div>
              <h3 class="section-title">图纸文件</h3>
              <p class="section-hint">上传 PDF、图片或工程图截图，系统会先识图再生成报价。</p>
            </div>
          </div>
          <div class="upload-panel">
            <input id="files" class="file-input-native" name="files" type="file" accept=".pdf,image/*,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" multiple required />
            <label id="upload-zone" class="upload-zone" for="files">
              <span class="upload-mark">+</span>
              <span>
                <span id="upload-title" class="upload-title">选择图纸文件</span>
                <span id="upload-hint" class="upload-hint">支持 PDF、PNG、JPG、WEBP、BMP、TIFF，可一次上传多个文件。</span>
              </span>
            </label>
            <div id="file-summary" class="file-summary" hidden></div>
          </div>
        </section>
        <section class="form-section">
          <div>
            <h3 class="section-title">报价需求</h3>
          <p class="section-hint">选择常用场景并补充特殊要求，系统会自动生成识别和报价指令。</p>
          </div>
          <div class="request-grid">
            <div class="request-field">
              <label for="quote_subject">对象</label>
              <select id="quote_subject">
                <option value="自动识别图纸品类">自动识别</option>
                <option value="铜排 / 铜母排">铜排 / 铜母排</option>
                <option value="铝排 / 铝母排">铝排 / 铝母排</option>
                <option value="铜软连接 / 编织线">铜软连接 / 编织线</option>
                <option value="钣金件">钣金件</option>
                <option value="绝缘件 / 绝缘纸">绝缘件 / 绝缘纸</option>
                <option value="紧固件 / 螺栓类">紧固件 / 螺栓类</option>
                <option value="其他 / 自定义">其他 / 自定义</option>
              </select>
            </div>
            <div class="request-field">
              <label for="quote_goal">目标</label>
              <select id="quote_goal">
                <option value="生成标准报价报告">生成标准报价报告</option>
                <option value="只识别图纸参数，不生成报价">只识别图纸参数</option>
                <option value="生成成本明细">生成成本明细</option>
                <option value="生成工艺路线和成本明细">生成工艺路线</option>
                <option value="复核已有报价是否合理">复核已有报价</option>
                <option value="批量图纸汇总报价">批量汇总报价</option>
              </select>
            </div>
            <div class="request-field">
              <label for="quote_detail">输出详细程度</label>
              <select id="quote_detail">
                <option value="标准报告，包含关键尺寸、材料、工艺、成本估算和待确认项">标准</option>
                <option value="简版报告，突出结论和关键待确认项">简版</option>
                <option value="详细成本拆分，包含材料、加工、表面处理、损耗、包装和风险项">详细成本拆分</option>
                <option value="重点列出缺失参数和待确认项">含待确认项</option>
                <option value="包含审核意见和修正建议">含审核意见</option>
                <option value="包含风险提示、识别置信度和不确定项">含风险提示和置信度</option>
              </select>
            </div>
            <div class="request-field">
              <label for="quote_process">工艺重点</label>
              <select id="quote_process">
                <option value="自动判断图纸涉及的主要工艺">自动判断</option>
                <option value="重点识别激光下料">激光下料</option>
                <option value="重点识别折弯和展开尺寸">折弯</option>
                <option value="重点识别冲孔 / 钻孔 / 孔径孔位">冲孔 / 钻孔</option>
                <option value="重点识别攻牙 / 沉孔 / 倒角">攻牙 / 沉孔</option>
                <option value="重点识别焊接 / 铆接 / 连接结构">焊接 / 铆接</option>
                <option value="重点识别镀锡 / 镀镍 / 镀银等表面处理">镀锡 / 镀镍 / 镀银</option>
                <option value="重点识别喷涂 / 氧化 / 绝缘处理">喷涂 / 氧化</option>
                <option value="重点识别热缩 / 绝缘包覆 / 套管">热缩 / 绝缘包覆</option>
                <option value="重点识别检验、包装和运输要求">检验包装</option>
              </select>
            </div>
            <div class="request-field full">
              <label for="quote_extra">补充要求</label>
              <textarea id="quote_extra" rows="3" placeholder="例如：数量 500 件，材料 T2-Y，铜价按 76 元/kg，含税不含运费，表面镀锡，缺少参数请列为待确认项。">缺少参数请列为待确认项。</textarea>
            </div>
            <div class="request-field full">
              <div id="prompt-preview" class="prompt-preview" aria-live="polite"></div>
            </div>
          </div>
          <textarea id="prompt" class="prompt-hidden" name="prompt" required>识别图纸品类并生成报价报告。缺少参数请列为待确认项。</textarea>
        </section>
        <section class="form-section">
          <div>
            <h3 class="section-title">高级设置</h3>
          </div>
          <div class="settings-grid">
            <div class="setting-card">
              <label for="max_review_rounds">审核重跑轮数</label>
              <input id="max_review_rounds" name="max_review_rounds" type="number" value="2" min="0" max="5" />
            </div>
            <label class="switch-control" for="audit">
              <span class="switch-text">
                <span class="setting-title">显示审核记录</span>
                <span class="switch-hint">在最终报告末尾附上审核结果。</span>
              </span>
              <input id="audit" name="audit" type="checkbox" value="true" />
              <span class="switch" aria-hidden="true"></span>
            </label>
          </div>
        </section>
        <div class="submit-row">
          <button type="submit">开始报价</button>
        </div>
      </form>
      <div id="quote-status" class="status" hidden></div>
      <div id="quote-assets" class="assets" hidden></div>
      <div id="result-actions" class="result-actions" hidden>
        <a id="excel-download" class="result-action" href="#" target="_blank" rel="noopener">下载 Excel 成本表</a>
      </div>
      <section id="quote-report" class="report" hidden></section>
      <section id="quote-feedback" class="feedback-panel" hidden>
        <div class="feedback-header">
          <div class="feedback-copy">
            <h3>结果校验</h3>
            <p>用于沉淀识别样本和知识库规则。</p>
          </div>
          <div class="feedback-buttons" role="group" aria-label="结果校验">
            <button id="feedback-qualified" class="feedback-choice qualified" type="button" data-feedback-verdict="qualified">合格</button>
            <button id="feedback-unqualified" class="feedback-choice unqualified" type="button" data-feedback-verdict="unqualified">不合格</button>
          </div>
        </div>
        <textarea id="feedback-note" class="feedback-note" rows="3" maxlength="2000" placeholder="可选：记录问题位置、正确结论或修改建议"></textarea>
        <div id="feedback-state" class="feedback-state" aria-live="polite"></div>
      </section>
    </div>
  </section>
</main>
<div id="image-viewer" class="viewer" hidden role="dialog" aria-modal="true">
  <div class="viewer-toolbar">
    <div id="viewer-title" class="viewer-title"></div>
    <button type="button" data-viewer-action="zoom-out" title="Zoom out">-</button>
    <button type="button" data-viewer-action="zoom-in" title="Zoom in">+</button>
    <button type="button" data-viewer-action="fit" title="Fit">Fit</button>
    <button type="button" data-viewer-action="actual" title="Actual size">1:1</button>
    <a id="viewer-open" href="#" target="_blank" rel="noopener" title="Open in new page">Open</a>
    <button type="button" data-viewer-action="close" title="Close">X</button>
  </div>
  <div id="viewer-stage" class="viewer-stage">
    <img id="viewer-image" alt="" />
  </div>
</div>
<div id="confirm-dialog" class="dialog" hidden role="dialog" aria-modal="true" aria-labelledby="confirm-title">
  <div class="dialog-card">
    <h2 id="confirm-title">删除报价记录</h2>
    <p id="confirm-message">删除后，该记录将不再显示在你的历史记录中。</p>
    <div class="dialog-actions">
      <button id="confirm-cancel" class="dialog-cancel" type="button">取消</button>
      <button id="confirm-delete" class="danger-button" type="button">删除</button>
    </div>
  </div>
</div>
<script>
(() => {
  const form = document.querySelector("#quote-form");
  const button = form.querySelector("button[type=submit]");
  const fileInput = form.querySelector("#files");
  const promptInput = form.querySelector("#prompt");
  const quoteSubject = form.querySelector("#quote_subject");
  const quoteGoal = form.querySelector("#quote_goal");
  const quoteDetail = form.querySelector("#quote_detail");
  const quoteProcess = form.querySelector("#quote_process");
  const quoteExtra = form.querySelector("#quote_extra");
  const promptPreview = form.querySelector("#prompt-preview");
  const uploadZone = document.querySelector("#upload-zone");
  const uploadTitle = document.querySelector("#upload-title");
  const uploadHint = document.querySelector("#upload-hint");
  const fileSummary = document.querySelector("#file-summary");
  const statusBox = document.querySelector("#quote-status");
  const assetsBox = document.querySelector("#quote-assets");
  const resultActions = document.querySelector("#result-actions");
  const excelDownload = document.querySelector("#excel-download");
  const reportBox = document.querySelector("#quote-report");
  const feedbackPanel = document.querySelector("#quote-feedback");
  const feedbackNote = document.querySelector("#feedback-note");
  const feedbackState = document.querySelector("#feedback-state");
  const feedbackButtons = Array.from(document.querySelectorAll("[data-feedback-verdict]"));
  const historyBox = document.querySelector("#quote-history");
  const paginationBox = document.querySelector("#history-pagination");
  const historySummary = document.querySelector("#history-summary");
  const refreshHistoryButton = document.querySelector("#refresh-history");
  const historySearchInput = document.querySelector("#history-search");
  const newQuoteButton = document.querySelector("#new-quote");
  const workspace = document.querySelector(".workspace");
  const viewer = document.querySelector("#image-viewer");
  const viewerTitle = document.querySelector("#viewer-title");
  const viewerStage = document.querySelector("#viewer-stage");
  const viewerImage = document.querySelector("#viewer-image");
  const viewerOpen = document.querySelector("#viewer-open");
  const confirmDialog = document.querySelector("#confirm-dialog");
  const confirmMessage = document.querySelector("#confirm-message");
  const confirmCancel = document.querySelector("#confirm-cancel");
  const confirmDelete = document.querySelector("#confirm-delete");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const PAGE_SIZE = 10;
  let currentJobId = "";
  let historyJobs = [];
  let historyPageCache = new Map();
  let historyPage = 1;
  let historyPagination = { page: 1, page_size: PAGE_SIZE, total_pages: 0, total_items: 0, is_admin: false };
  let isSubmitting = false;
  let pendingDeleteJob = null;
  let viewerScale = 1;
  let naturalWidth = 1;
  let naturalHeight = 1;
  let currentFeedbackUrl = "";
  let currentFeedbackVerdict = "";

  const setStatus = (message, isError = false) => {
    statusBox.hidden = false;
    statusBox.textContent = message;
    statusBox.classList.toggle("error", isError);
  };

  const readJson = async (response) => {
    try {
      return await response.json();
    } catch {
      return {};
    }
  };

  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));

  const renderInlineMarkdown = (value) => {
    let html = escapeHtml(value);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/\\[([^\\]]+)\\]\\(((?:https?:\\/\\/|\\/)[^)\\s]+)\\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return html;
  };

  const appendInlineBlock = (parent, tagName, text) => {
    const element = document.createElement(tagName);
    element.innerHTML = renderInlineMarkdown(text);
    parent.appendChild(element);
    return element;
  };

  const splitTableRow = (line) => line
    .trim()
    .replace(/^\\|/, "")
    .replace(/\\|$/, "")
    .split("|")
    .map((cell) => cell.trim());

  const isTableSeparator = (line) => {
    const cells = splitTableRow(line);
    return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\\s+/g, "")));
  };

  const isTableStart = (lines, index) => (
    index + 1 < lines.length
    && lines[index].includes("|")
    && isTableSeparator(lines[index + 1])
  );

  const lineBlockType = (lines, index) => {
    const line = lines[index] || "";
    const trimmed = line.trim();
    if (!trimmed) {
      return "blank";
    }
    if (trimmed.startsWith("```")) {
      return "code";
    }
    if (/^#{1,6}\\s+/.test(trimmed)) {
      return "heading";
    }
    if (/^([-*_])\\s*\\1\\s*\\1\\s*$/.test(trimmed)) {
      return "rule";
    }
    if (/^\\s*([-*+])\\s+/.test(line) || /^\\s*\\d+[.)]\\s+/.test(line)) {
      return "list";
    }
    if (/^\\s*>\\s?/.test(line)) {
      return "quote";
    }
    if (isTableStart(lines, index)) {
      return "table";
    }
    return "paragraph";
  };

  const renderMarkdown = (markdown, container) => {
    container.replaceChildren();
    const lines = String(markdown || "").replace(/\\r\\n?/g, "\\n").split("\\n");
    let index = 0;

    while (index < lines.length) {
      const type = lineBlockType(lines, index);
      const line = lines[index] || "";
      const trimmed = line.trim();

      if (type === "blank") {
        index += 1;
      } else if (type === "code") {
        const codeLines = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith("```")) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) {
          index += 1;
        }
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\\n");
        pre.appendChild(code);
        container.appendChild(pre);
      } else if (type === "heading") {
        const match = trimmed.match(/^(#{1,6})\\s+(.+)$/);
        appendInlineBlock(container, `h${match[1].length}`, match[2]);
        index += 1;
      } else if (type === "rule") {
        container.appendChild(document.createElement("hr"));
        index += 1;
      } else if (type === "list") {
        const ordered = /^\\s*\\d+[.)]\\s+/.test(line);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (index < lines.length) {
          const current = lines[index] || "";
          const match = ordered
            ? current.match(/^\\s*\\d+[.)]\\s+(.+)$/)
            : current.match(/^\\s*[-*+]\\s+(.+)$/);
          if (!match) {
            break;
          }
          appendInlineBlock(list, "li", match[1]);
          index += 1;
        }
        container.appendChild(list);
      } else if (type === "quote") {
        const quoteLines = [];
        while (index < lines.length && /^\\s*>\\s?/.test(lines[index] || "")) {
          quoteLines.push((lines[index] || "").replace(/^\\s*>\\s?/, ""));
          index += 1;
        }
        const blockquote = document.createElement("blockquote");
        appendInlineBlock(blockquote, "p", quoteLines.join(" "));
        container.appendChild(blockquote);
      } else if (type === "table") {
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const tbody = document.createElement("tbody");
        const headerRow = document.createElement("tr");
        for (const cellText of splitTableRow(lines[index])) {
          const cell = document.createElement("th");
          cell.innerHTML = renderInlineMarkdown(cellText);
          headerRow.appendChild(cell);
        }
        thead.appendChild(headerRow);
        index += 2;
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          const row = document.createElement("tr");
          for (const cellText of splitTableRow(lines[index])) {
            const cell = document.createElement("td");
            cell.innerHTML = renderInlineMarkdown(cellText);
            row.appendChild(cell);
          }
          tbody.appendChild(row);
          index += 1;
        }
        table.append(thead, tbody);
        container.appendChild(table);
      } else {
        const paragraphLines = [];
        while (index < lines.length && lineBlockType(lines, index) === "paragraph") {
          paragraphLines.push((lines[index] || "").trim());
          index += 1;
        }
        appendInlineBlock(container, "p", paragraphLines.join(" "));
      }
    }
  };

  const showReport = (markdown) => {
    reportBox.hidden = false;
    renderMarkdown(markdown, reportBox);
  };

  const setExcelDownload = (url, audit = null) => {
    if (!url) {
      resultActions.hidden = true;
      excelDownload.removeAttribute("href");
      return;
    }
    excelDownload.href = url;
    const verdict = audit && audit.verdict ? audit.verdict : "";
    excelDownload.textContent = verdict === "needs_confirmation" ? "下载待确认 Excel" : "下载 Excel 成本表";
    resultActions.hidden = false;
  };

  const feedbackLabel = (verdict) => verdict === "qualified" ? "合格" : "不合格";

  const updateFeedbackButtons = () => {
    for (const feedbackButton of feedbackButtons) {
      const verdict = feedbackButton.dataset.feedbackVerdict || "";
      feedbackButton.classList.toggle("selected", verdict === currentFeedbackVerdict);
    }
  };

  const setFeedbackState = (message, isError = false) => {
    feedbackState.textContent = message;
    feedbackState.classList.toggle("error", isError);
  };

  const hideFeedbackPanel = () => {
    feedbackPanel.hidden = true;
    currentFeedbackUrl = "";
    currentFeedbackVerdict = "";
    feedbackNote.value = "";
    setFeedbackState("");
    updateFeedbackButtons();
  };

  const showFeedbackPanel = (statusData = {}) => {
    currentFeedbackUrl = statusData.feedback_url || (currentJobId ? `/api/jobs/${currentJobId}/feedback` : "");
    const feedback = statusData.feedback || null;
    currentFeedbackVerdict = feedback && feedback.verdict ? feedback.verdict : "";
    feedbackNote.value = feedback && typeof feedback.note === "string" ? feedback.note : "";
    feedbackPanel.hidden = !currentFeedbackUrl;
    updateFeedbackButtons();
    setFeedbackState(currentFeedbackVerdict ? `已标记：${feedbackLabel(currentFeedbackVerdict)}` : "请选择合格或不合格");
  };

  const submitFeedback = async (verdict) => {
    if (!currentFeedbackUrl) {
      return;
    }

    currentFeedbackVerdict = verdict;
    updateFeedbackButtons();
    setFeedbackState("正在保存...");
    for (const feedbackButton of feedbackButtons) {
      feedbackButton.disabled = true;
    }

    try {
      const response = await fetch(currentFeedbackUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verdict, note: feedbackNote.value.trim() }),
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.detail || data.error || `保存失败：${response.status}`);
      }
      const saved = data.feedback || {};
      currentFeedbackVerdict = saved.verdict || verdict;
      if (typeof saved.note === "string") {
        feedbackNote.value = saved.note;
      }
      updateFeedbackButtons();
      setFeedbackState(`已保存：${feedbackLabel(currentFeedbackVerdict)}`);
    } catch (error) {
      setFeedbackState(error.message || String(error), true);
    } finally {
      for (const feedbackButton of feedbackButtons) {
        feedbackButton.disabled = false;
      }
    }
  };

  const hasFiles = () => fileInput.files && fileInput.files.length > 0;

  const buildPrompt = () => {
    const parts = [
      `报价对象：${quoteSubject.value}`,
      `报价目标：${quoteGoal.value}`,
      `输出详细程度：${quoteDetail.value}`,
      `工艺重点：${quoteProcess.value}`,
    ];
    const extra = quoteExtra.value.trim();
    if (extra) {
      parts.push(`补充要求：${extra}`);
    }
    parts.push("请基于上传图纸识别关键尺寸、材料、工艺、数量相关假设、成本构成和待确认项；无法确定的信息不要编造。");
    return parts.join("\\n");
  };

  const updatePrompt = () => {
    const prompt = buildPrompt();
    promptInput.value = prompt;
    promptPreview.innerHTML = `<strong>提交内容预览</strong>\n${escapeHtml(prompt)}`;
  };

  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
  };

  const updateFileSummary = () => {
    const files = Array.from(fileInput.files || []);
    uploadZone.classList.toggle("has-files", files.length > 0);
    fileSummary.replaceChildren();

    if (!files.length) {
      fileSummary.hidden = true;
      uploadTitle.textContent = "选择图纸文件";
      uploadHint.textContent = "支持 PDF、PNG、JPG、WEBP、BMP、TIFF，可一次上传多个文件。";
      return;
    }

    fileSummary.hidden = false;
    uploadTitle.textContent = files.length === 1 ? "已选择 1 个文件" : `已选择 ${files.length} 个文件`;
    uploadHint.textContent = "可以直接开始报价，或重新点击此区域更换文件。";

    for (const file of files.slice(0, 5)) {
      const item = document.createElement("div");
      item.className = "file-pill";
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = file.name;
      const size = document.createElement("span");
      size.className = "file-size";
      size.textContent = formatBytes(file.size);
      item.append(name, size);
      fileSummary.appendChild(item);
    }

    if (files.length > 5) {
      const more = document.createElement("div");
      more.className = "file-pill";
      more.textContent = `另有 ${files.length - 5} 个文件`;
      fileSummary.appendChild(more);
    }
  };

  const updateSubmitState = () => {
    button.disabled = isSubmitting || !hasFiles();
    if (!isSubmitting) {
      button.textContent = hasFiles() ? "开始报价" : "先选择文件";
    }
  };

  const resetResultView = () => {
    assetsBox.hidden = true;
    assetsBox.textContent = "";
    assetsBox.dataset.loaded = "false";
    setExcelDownload("");
    reportBox.hidden = true;
    reportBox.replaceChildren();
    hideFeedbackPanel();
  };

  const resetFormView = () => {
    form.reset();
    updatePrompt();
    updateFileSummary();
    updateSubmitState();
  };

  const formatDate = (value) => {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
  };

  const shortJobId = (jobId) => jobId ? jobId.slice(0, 8) : "";

  const statusText = (status) => ({
    queued: "排队中",
    running: "识别中",
    draft_ready: "初版已出",
    completed: "已完成",
    failed: "失败",
  }[status] || status || "未知");

  const searchableJobText = (job) => {
    const fileNames = Array.isArray(job.file_names) ? job.file_names : [];
    return [
      job.prompt,
      job.job_id,
      job.username,
      job.status,
      job.admin_deleted_at ? "管理员已删除" : "",
      ...fileNames,
    ].filter(Boolean).join(" ").toLowerCase();
  };

  const filteredHistoryJobs = () => {
    const query = historySearchInput.value.trim().toLowerCase();
    if (!query) {
      return historyJobs;
    }
    return historyJobs.filter((job) => searchableJobText(job).includes(query));
  };

  const scrollWorkspaceTop = () => {
    if (workspace) {
      workspace.scrollTo({ top: 0, behavior: "smooth" });
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const centerViewer = () => {
    requestAnimationFrame(() => {
      viewerStage.scrollLeft = Math.max((viewerStage.scrollWidth - viewerStage.clientWidth) / 2, 0);
      viewerStage.scrollTop = Math.max((viewerStage.scrollHeight - viewerStage.clientHeight) / 2, 0);
    });
  };

  const setViewerScale = (scale) => {
    viewerScale = Math.max(0.1, Math.min(scale, 6));
    viewerImage.style.width = `${Math.round(naturalWidth * viewerScale)}px`;
    viewerImage.style.height = "auto";
    centerViewer();
  };

  const fitViewer = () => {
    const bounds = viewerStage.getBoundingClientRect();
    const widthScale = (bounds.width - 32) / naturalWidth;
    const heightScale = (bounds.height - 32) / naturalHeight;
    setViewerScale(Math.min(widthScale, heightScale, 1));
  };

  const openViewer = (url, label) => {
    viewer.hidden = false;
    viewerTitle.textContent = label || "attachment";
    viewerOpen.href = url;
    viewerImage.alt = label || "attachment";
    viewerImage.onload = () => {
      naturalWidth = viewerImage.naturalWidth || 1;
      naturalHeight = viewerImage.naturalHeight || 1;
      setViewerScale(1);
    };
    viewerImage.src = url;
  };

  const closeViewer = () => {
    viewer.hidden = true;
    viewerImage.removeAttribute("src");
  };

  viewer.addEventListener("click", (event) => {
    if (event.target === viewerStage) {
      closeViewer();
    }
  });

  viewer.addEventListener("click", (event) => {
    const action = event.target instanceof HTMLElement ? event.target.dataset.viewerAction : "";
    if (!action) {
      return;
    }

    if (action === "zoom-in") {
      setViewerScale(viewerScale * 1.25);
    } else if (action === "zoom-out") {
      setViewerScale(viewerScale / 1.25);
    } else if (action === "fit") {
      fitViewer();
    } else if (action === "actual") {
      setViewerScale(1);
    } else if (action === "close") {
      closeViewer();
    }
  });

  viewerStage.addEventListener("dblclick", () => {
    setViewerScale(viewerScale < 1 ? 1 : viewerScale * 1.5);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !viewer.hidden) {
      closeViewer();
    } else if (event.key === "Escape" && !confirmDialog.hidden) {
      closeDeleteDialog();
    }
  });

  window.addEventListener("resize", () => {
    if (!viewer.hidden) {
      fitViewer();
    }
  });

  const renderAssets = async (assetsUrl, force = false) => {
    if (!assetsUrl || (!force && assetsBox.dataset.loaded === "true")) {
      return;
    }

    let response;
    try {
      response = await fetch(assetsUrl);
      if (!response.ok) {
        return;
      }
    } catch {
      return;
    }

    const data = await readJson(response);
    const assets = Array.isArray(data.assets) ? data.assets : [];
    if (!assets.length) {
      return;
    }

    assetsBox.textContent = "";
    assetsBox.hidden = false;
    assetsBox.dataset.loaded = "true";

    const renderAssetCard = (asset) => {
      const card = document.createElement("figure");
      card.className = "asset";
      const objectUrl = asset.url;

      if (asset.type === "image") {
        card.classList.add("image-card");
        const previewButton = document.createElement("button");
        previewButton.type = "button";
        previewButton.className = "asset-preview";
        previewButton.title = "Open viewer";
        previewButton.addEventListener("click", () => {
          openViewer(objectUrl, asset.label || asset.mime_type || "attachment");
        });

        const image = document.createElement("img");
        image.alt = asset.label || "attachment";
        image.loading = "lazy";
        image.decoding = "async";
        image.src = objectUrl;
        previewButton.appendChild(image);
        card.appendChild(previewButton);
      }

      const label = document.createElement("a");
      label.href = objectUrl;
      label.target = "_blank";
      label.rel = "noopener";
      label.textContent = asset.label || asset.mime_type || "attachment";
      card.appendChild(label);
      assetsBox.appendChild(card);
    };

    const initialLimit = 6;
    for (const asset of assets.slice(0, initialLimit)) {
      renderAssetCard(asset);
    }

    if (assets.length > initialLimit) {
      const moreButton = document.createElement("button");
      moreButton.type = "button";
      moreButton.className = "assets-more";
      moreButton.textContent = `显示全部 ${assets.length} 个文件`;
      moreButton.addEventListener("click", () => {
        moreButton.remove();
        for (const asset of assets.slice(initialLimit)) {
          renderAssetCard(asset);
        }
      });
      assetsBox.appendChild(moreButton);
    }
  };

  const markActiveHistory = () => {
    for (const item of historyBox.querySelectorAll(".history-item")) {
      item.classList.toggle("active", item.dataset.jobId === currentJobId);
    }
  };

  const openHistoryJob = async (job) => {
    if (job.admin_deleted_at && !historyPagination.is_admin) {
      currentJobId = job.job_id;
      markActiveHistory();
      resetResultView();
      scrollWorkspaceTop();
      setStatus("该报价记录已由管理员删除。", true);
      return;
    }

    currentJobId = job.job_id;
    markActiveHistory();
    resetResultView();
    scrollWorkspaceTop();
    setStatus(`正在打开报价记录 ${shortJobId(job.job_id)}...`);

    const statusResponse = await fetch(job.status_url);
    const statusData = await readJson(statusResponse);
    if (!statusResponse.ok) {
      throw new Error(statusData.detail || statusData.error || `打开记录失败：${statusResponse.status}`);
    }

    await renderAssets(statusData.assets_url || job.assets_url, true);
    setExcelDownload(statusData.excel_url || "", statusData.excel_audit || null);

    if (["draft_ready", "completed"].includes(statusData.status)) {
      const reportResponse = await fetch(statusData.report_url || job.report_url);
      const reportText = await reportResponse.text();
      if (reportResponse.ok) {
        showReport(reportText);
      }
      showFeedbackPanel(statusData);
    } else {
      hideFeedbackPanel();
    }

    const review = statusData.review_status ? `，审核：${statusData.review_status}` : "";
    const excelAudit = statusData.excel_audit && statusData.excel_audit.verdict
      ? `，Excel：${statusData.excel_audit.verdict}`
      : "";
    setStatus(`已打开报价记录 ${shortJobId(job.job_id)}，状态：${statusText(statusData.status)}${review}${excelAudit}`);
  };

  const openDeleteDialog = (job) => {
    pendingDeleteJob = job;
    confirmMessage.textContent = historyPagination.is_admin
      ? "删除后，管理员列表将不再展示该记录；普通用户页面会显示管理员已删除。数据库记录和上传文件不会物理清理。"
      : "删除后，该记录将不再显示在你的历史记录中。管理员仍可查看完整记录。";
    confirmDelete.disabled = false;
    confirmDelete.textContent = "删除";
    confirmDialog.hidden = false;
    confirmCancel.focus({ preventScroll: true });
  };

  const closeDeleteDialog = () => {
    confirmDialog.hidden = true;
    pendingDeleteJob = null;
    confirmDelete.disabled = false;
    confirmDelete.textContent = "删除";
  };

  const deleteHistoryJob = async () => {
    if (!pendingDeleteJob) {
      return;
    }

    const job = pendingDeleteJob;
    confirmDelete.disabled = true;
    confirmDelete.textContent = "删除中...";
    const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}`, { method: "DELETE" });
    const data = await readJson(response);
    if (!response.ok) {
      confirmDelete.disabled = false;
      confirmDelete.textContent = "删除";
      throw new Error(data.detail || data.error || `删除失败：${response.status}`);
    }

    closeDeleteDialog();
    if (currentJobId === job.job_id) {
      currentJobId = "";
      resetResultView();
      statusBox.hidden = true;
      statusBox.classList.remove("error");
    }
    setStatus(historyPagination.is_admin ? "报价记录已从管理员列表删除。" : "已从历史记录中删除。");
    historyPageCache.clear();
    const shouldStepBack = historyJobs.length <= 1 && historyPage > 1;
    await loadHistory(shouldStepBack ? historyPage - 1 : historyPage, { force: true });
  };

  const visiblePageJobs = () => filteredHistoryJobs().slice(0, PAGE_SIZE);

  const promptLineValue = (prompt, label) => {
    const match = String(prompt || "").match(new RegExp(`^${label}：(.+)$`, "m"));
    return match ? match[1].trim() : "";
  };

  const compactGoalText = (goal) => ({
    "生成标准报价报告": "标准报价报告",
    "只识别图纸参数，不生成报价": "图纸参数识别",
    "生成成本明细": "成本明细",
    "生成工艺路线和成本明细": "工艺路线",
    "复核已有报价是否合理": "报价复核",
    "批量图纸汇总报价": "批量汇总",
  }[goal] || goal);

  const compactSubjectText = (subject) => (
    subject === "自动识别图纸品类" ? "自动识别" : subject
  );

  const historyTitle = (job) => {
    const subject = compactSubjectText(promptLineValue(job.prompt, "报价对象"));
    const goal = compactGoalText(promptLineValue(job.prompt, "报价目标"));
    if (subject && goal) {
      return `${subject} · ${goal}`;
    }
    if (subject) {
      return subject;
    }
    if (goal) {
      return goal;
    }
    const prompt = String(job.prompt || "").replace(/\\s+/g, " ").trim();
    if (prompt.includes("识别图纸品类并生成报价报告")) {
      return "自动识别 · 标准报价报告";
    }
    if (prompt.includes("生成成本明细")) {
      return "自动识别 · 成本明细";
    }
    if (prompt.includes("工艺路线")) {
      return "自动识别 · 工艺路线";
    }
    if (prompt.includes("复核") || prompt.includes("审核")) {
      return "自动识别 · 报价复核";
    }
    return job.prompt || `报价记录 ${shortJobId(job.job_id)}`;
  };

  const paginationPages = () => {
    const totalPages = Number(historyPagination.total_pages || 0);
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, index) => index + 1);
    }
    const pages = new Set([1, totalPages, historyPage - 1, historyPage, historyPage + 1]);
    return Array.from(pages)
      .filter((page) => page >= 1 && page <= totalPages)
      .sort((a, b) => a - b);
  };

  const renderPagination = () => {
    paginationBox.replaceChildren();
    const totalPages = Number(historyPagination.total_pages || 0);
    const totalItems = Number(historyPagination.total_items || 0);
    const query = historySearchInput.value.trim();
    const filteredCount = filteredHistoryJobs().length;

    if (!totalPages) {
      historySummary.textContent = "";
      return;
    }

    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "page-button";
    previous.textContent = "‹";
    previous.disabled = historyPage <= 1;
    previous.addEventListener("click", () => loadHistory(historyPage - 1));
    paginationBox.appendChild(previous);

    let lastPage = 0;
    for (const page of paginationPages()) {
      if (lastPage && page - lastPage > 1) {
        const ellipsis = document.createElement("span");
        ellipsis.className = "page-ellipsis";
        ellipsis.textContent = "...";
        paginationBox.appendChild(ellipsis);
      }
      const pageButton = document.createElement("button");
      pageButton.type = "button";
      pageButton.className = "page-button";
      pageButton.classList.toggle("active", page === historyPage);
      pageButton.textContent = String(page);
      pageButton.disabled = page === historyPage;
      pageButton.addEventListener("click", () => loadHistory(page));
      paginationBox.appendChild(pageButton);
      lastPage = page;
    }

    const next = document.createElement("button");
    next.type = "button";
    next.className = "page-button";
    next.textContent = "›";
    next.disabled = historyPage >= totalPages;
    next.addEventListener("click", () => loadHistory(historyPage + 1));
    paginationBox.appendChild(next);

    const rangeStart = Math.min((historyPage - 1) * PAGE_SIZE + 1, totalItems);
    const rangeEnd = Math.min(historyPage * PAGE_SIZE, totalItems);
    const cap = historyPagination.max_items ? `，最多显示 ${historyPagination.max_items} 条` : "";
    historySummary.textContent = query
      ? `当前页匹配 ${filteredCount} 条 · 第 ${historyPage}/${totalPages} 页`
      : `第 ${historyPage}/${totalPages} 页 · ${rangeStart}-${rangeEnd} / ${totalItems}${cap}`;
  };

  const renderHistory = (jobs) => {
    historyBox.textContent = "";
    const visibleJobs = jobs.slice(0, PAGE_SIZE);
    if (!visibleJobs.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = historySearchInput.value.trim() ? "没有匹配的报价记录" : "暂无报价记录";
      historyBox.appendChild(empty);
      renderPagination();
      return;
    }

    for (const job of visibleJobs) {
      const item = document.createElement("div");
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.className = "history-item";
      item.classList.toggle("admin-deleted", Boolean(job.admin_deleted_at));
      item.dataset.jobId = job.job_id;

      const titleRow = document.createElement("div");
      titleRow.className = "history-title-row";

      const title = document.createElement("div");
      title.className = "history-title";
      title.textContent = historyTitle(job);

      const badge = document.createElement("span");
      badge.className = "status-badge";
      if (["queued", "running", "draft_ready", "completed", "failed"].includes(job.status)) {
        badge.classList.add(job.status);
      }
      badge.textContent = statusText(job.status);

      const actions = document.createElement("span");
      actions.className = "history-actions";
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "history-delete";
      deleteButton.title = "删除";
      deleteButton.setAttribute("aria-label", "删除报价记录");
      deleteButton.textContent = "×";
      deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openDeleteDialog(job);
      });
      actions.append(badge, deleteButton);
      titleRow.append(title, actions);

      const meta = document.createElement("div");
      meta.className = "history-meta";
      const owner = job.username ? ` · ${job.username}` : "";
      const deleted = job.admin_deleted_at ? ` · 管理员已删除` : "";
      meta.textContent = `${formatDate(job.updated_at || job.created_at)} · ${shortJobId(job.job_id)}${owner}${deleted}`;

      const files = document.createElement("div");
      files.className = "history-files";
      const fileNames = Array.isArray(job.file_names) ? job.file_names.filter(Boolean) : [];
      files.textContent = fileNames.length ? fileNames.join("，") : "无上传文件";

      item.append(titleRow, meta, files);
      item.addEventListener("click", async () => {
        try {
          await openHistoryJob(job);
        } catch (error) {
          setStatus(error.message || String(error), true);
        }
      });
      item.addEventListener("keydown", async (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        try {
          await openHistoryJob(job);
        } catch (error) {
          setStatus(error.message || String(error), true);
        }
      });
      historyBox.appendChild(item);
    }
    markActiveHistory();
    renderPagination();
  };

  const jobsForPage = (page) => historyPageCache.get(page) || [];

  const applyHistoryPage = (page) => {
    historyPage = Math.max(1, Number(page) || 1);
    historyJobs = jobsForPage(historyPage);
    historyPagination = { ...historyPagination, page: historyPage };
    renderHistory(filteredHistoryJobs());
  };

  const fetchHistoryWindow = async (page, prefetchPages = 2) => {
    const safePage = Math.max(1, Number(page) || 1);
    const response = await fetch(`/api/jobs/history?page=${safePage}&page_size=${PAGE_SIZE}&prefetch_pages=${prefetchPages}`);
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(data.detail || data.error || `获取报价记录失败：${response.status}`);
    }

    historyPagination = data.pagination || historyPagination;
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    for (let index = 0; index < jobs.length; index += PAGE_SIZE) {
      historyPageCache.set(safePage + (index / PAGE_SIZE), jobs.slice(index, index + PAGE_SIZE));
    }
    return Number(historyPagination.page || safePage);
  };

  const loadHistory = async (page = historyPage, options = {}) => {
    try {
      const safePage = Math.max(1, Number(page) || 1);
      if (!options.force && historyPageCache.has(safePage)) {
        applyHistoryPage(safePage);
        const nextPage = safePage + 1;
        if (nextPage <= Number(historyPagination.total_pages || 0) && !historyPageCache.has(nextPage)) {
          fetchHistoryWindow(nextPage, 1).catch(() => {});
        }
        return;
      }

      const resolvedPage = await fetchHistoryWindow(safePage, 2);
      historyPage = resolvedPage;
      historyJobs = jobsForPage(historyPage);
      renderHistory(filteredHistoryJobs());
    } catch (error) {
      historyBox.textContent = "";
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = error.message || String(error);
      historyBox.appendChild(empty);
      paginationBox.replaceChildren();
      historySummary.textContent = "";
    }
  };

  form.addEventListener("submit", async (event) => {
    if (!window.fetch || !window.FormData) {
      return;
    }

    event.preventDefault();
    if (!hasFiles()) {
      setStatus("请先上传图纸 / PDF / 图片。", true);
      updateSubmitState();
      return;
    }

    isSubmitting = true;
    button.textContent = "报价中...";
    updateSubmitState();
    resetResultView();

    try {
      setStatus("正在上传附件...");
      updatePrompt();
      const formData = new FormData(form);

      const createResponse = await fetch("/api/jobs", {
        method: "POST",
        body: formData,
      });
      const createData = await readJson(createResponse);
      if (!createResponse.ok) {
        throw new Error(createData.detail || createData.error || `创建任务失败：${createResponse.status}`);
      }

      currentJobId = createData.job_id || "";
      historySearchInput.value = "";
      historyPage = 1;
      historyPageCache.clear();
      await loadHistory(1, { force: true });
      setStatus("任务已创建，正在识别图纸...");
      await renderAssets(createData.assets_url);
      let finalStatus = {};
      while (true) {
        await sleep(2000);
        const statusResponse = await fetch(createData.status_url);
        const statusData = await readJson(statusResponse);
        if (!statusResponse.ok) {
          throw new Error(statusData.detail || statusData.error || `查询任务失败：${statusResponse.status}`);
        }

        await renderAssets(statusData.assets_url || createData.assets_url);

        if (statusData.status === "queued") {
          setStatus("任务排队中...");
        } else if (statusData.status === "running") {
          setStatus("正在识别、报价和审核...");
        } else if (statusData.status === "draft_ready") {
          setStatus("初版已生成，正在后台审核...");
          if (reportBox.hidden) {
            const draftResponse = await fetch(createData.report_url);
            const draftText = await draftResponse.text();
            if (draftResponse.ok) {
              showReport(draftText);
            }
          }
        } else if (statusData.status === "completed") {
          finalStatus = statusData;
          break;
        } else if (statusData.status === "failed") {
          throw new Error(statusData.error || "报价失败");
        }
      }

      await renderAssets(createData.assets_url);
      const reportResponse = await fetch(createData.report_url);
      const reportText = await reportResponse.text();
      if (!reportResponse.ok) {
        throw new Error(reportText || `获取报告失败：${reportResponse.status}`);
      }

      setStatus("初版报价已生成，审核结果：" + (finalStatus.review_status || "未知"));
      setExcelDownload(finalStatus.excel_url || "", finalStatus.excel_audit || null);
      showReport(reportText);
      showFeedbackPanel(finalStatus);
      await loadHistory(historyPage, { force: true });
    } catch (error) {
      setStatus(error.message || String(error), true);
    } finally {
      isSubmitting = false;
      updateSubmitState();
    }
  });

  for (const control of [quoteSubject, quoteGoal, quoteDetail, quoteProcess, quoteExtra]) {
    control.addEventListener("input", updatePrompt);
    control.addEventListener("change", updatePrompt);
  }

  fileInput.addEventListener("change", () => {
    if (hasFiles() && statusBox.classList.contains("error")) {
      statusBox.hidden = true;
      statusBox.classList.remove("error");
    }
    updateFileSummary();
    updateSubmitState();
  });

  uploadZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    uploadZone.classList.add("dragging");
  });

  uploadZone.addEventListener("dragleave", (event) => {
    if (!uploadZone.contains(event.relatedTarget)) {
      uploadZone.classList.remove("dragging");
    }
  });

  uploadZone.addEventListener("drop", (event) => {
    event.preventDefault();
    uploadZone.classList.remove("dragging");
    if (event.dataTransfer && event.dataTransfer.files.length) {
      fileInput.files = event.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });

  refreshHistoryButton.addEventListener("click", () => loadHistory(historyPage, { force: true }));
  for (const feedbackButton of feedbackButtons) {
    feedbackButton.addEventListener("click", () => submitFeedback(feedbackButton.dataset.feedbackVerdict || ""));
  }
  historySearchInput.addEventListener("input", () => {
    renderHistory(visiblePageJobs());
  });
  confirmCancel.addEventListener("click", closeDeleteDialog);
  confirmDialog.addEventListener("click", (event) => {
    if (event.target === confirmDialog) {
      closeDeleteDialog();
    }
  });
  confirmDelete.addEventListener("click", async () => {
    try {
      await deleteHistoryJob();
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  });
  newQuoteButton.addEventListener("click", () => {
    currentJobId = "";
    markActiveHistory();
    resetResultView();
    statusBox.hidden = true;
    statusBox.classList.remove("error");
    resetFormView();
    scrollWorkspaceTop();
    fileInput.focus({ preventScroll: true });
  });
  updateSubmitState();
  updatePrompt();
  updateFileSummary();
  loadHistory(1);
})();
</script>
</body>
</html>
"""


@app.post("/ui/quote", response_class=HTMLResponse)
async def ui_quote(
    request: Request,
    prompt: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    max_review_rounds: int = Form(default=2),
    audit: bool = Form(default=False),
    work_model: str | None = Form(default=None),
    vision_model: str | None = Form(default=None),
    review_model: str | None = Form(default=None),
) -> str:
    require_token(request)
    files = require_uploaded_files(files)
    job_id = uuid.uuid4().hex
    logger.info("ui quote request received job_id=%s uploads=%s", job_id, len(files or []))
    saved_uploads = await save_uploads(job_id, files)
    saved_files = saved_upload_paths(saved_uploads)
    logger.info("ui quote uploads saved job_id=%s saved_files=%s", job_id, len(saved_files))
    try:
        report = await generate_report(prompt, saved_files, max_review_rounds, audit, work_model, vision_model, review_model)
    except Exception as exc:
        message = html.escape(str(exc))
        return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>报价失败</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; background: #f6f7f9; color: #1f2937; }}
    main {{ max-width: 900px; margin: 0 auto; padding: 28px 20px; }}
    .box {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 20px; }}
    code {{ color: #b91c1c; word-break: break-word; }}
    a {{ color: #0f766e; }}
  </style>
</head>
<body>
<main>
  <p><a href="/">返回上传页面</a></p>
  <div class="box">
    <h1>报价失败</h1>
    <p>模型或配置返回错误，系统没有生成正式报价。</p>
    <p><code>{message}</code></p>
  </div>
</main>
</body>
</html>
"""
    escaped = html.escape(report)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>报价报告</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; background: #f6f7f9; color: #1f2937; }}
    main {{ max-width: 1000px; margin: 0 auto; padding: 28px 20px; }}
    pre {{ white-space: pre-wrap; background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 20px; line-height: 1.5; }}
    a {{ color: #0f766e; }}
  </style>
</head>
<body>
<main>
  <p><a href="/">返回上传页面</a></p>
  <pre>{escaped}</pre>
</main>
</body>
</html>
"""


@app.post("/api/quote")
async def api_quote(
    request: Request,
    prompt: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    max_review_rounds: int = Form(default=2),
    audit: bool = Form(default=False),
    work_model: str | None = Form(default=None),
    vision_model: str | None = Form(default=None),
    review_model: str | None = Form(default=None),
) -> JSONResponse:
    require_token(request)
    files = require_uploaded_files(files)
    job_id = uuid.uuid4().hex
    logger.info("api quote request received job_id=%s uploads=%s", job_id, len(files or []))
    saved_uploads = await save_uploads(job_id, files)
    saved_files = saved_upload_paths(saved_uploads)
    logger.info("api quote uploads saved job_id=%s saved_files=%s", job_id, len(saved_files))
    try:
        report = await generate_report(prompt, saved_files, max_review_rounds, audit, work_model, vision_model, review_model)
        return JSONResponse({"status": "completed", "report": report})
    except Exception as exc:
        return JSONResponse({"status": "failed", "error": str(exc)}, status_code=422)


@app.post("/api/jobs")
async def create_job(
    request: Request,
    prompt: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    max_review_rounds: int = Form(default=2),
    audit: bool = Form(default=False),
    work_model: str | None = Form(default=None),
    vision_model: str | None = Form(default=None),
    review_model: str | None = Form(default=None),
) -> JSONResponse:
    auth = require_token(request)
    files = require_uploaded_files(files)
    job_id = uuid.uuid4().hex
    logger.info("job quote request received job_id=%s uploads=%s", job_id, len(files or []))
    saved_uploads = await save_uploads(job_id, files)
    saved_files = saved_upload_paths(saved_uploads)
    logger.info("job quote uploads saved job_id=%s saved_files=%s", job_id, len(saved_files))
    record = JobRecord(
        job_id=job_id,
        status="queued",
        created_at=now_iso(),
        updated_at=now_iso(),
        prompt=prompt,
        user_id=auth.user_id,
    )
    write_status(record)
    try:
        upsert_job(record, saved_upload_file_names(saved_uploads))
        replace_job_files(record.job_id, saved_upload_records(saved_uploads))
    except Exception:
        logger.exception("quote database initial job sync failed job_id=%s", record.job_id)
    asyncio.create_task(run_job_langgraph(record, saved_files, max_review_rounds, audit, work_model, vision_model, review_model))
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/api/jobs/{job_id}",
            "report_url": f"/api/jobs/{job_id}/report",
            "assets_url": f"/api/jobs/{job_id}/assets",
            "feedback_url": f"/api/jobs/{job_id}/feedback",
        }
    )


@app.get("/api/jobs/history")
async def get_job_history(
    request: Request,
    page: int = 1,
    page_size: int = 10,
    prefetch_pages: int = 1,
    limit: int | None = None,
) -> dict:
    auth = require_token(request)
    include_all = auth.role == "admin"
    page_size = 10
    page = max(1, page)
    prefetch_pages = max(1, min(prefetch_pages, 2))

    if limit is not None and "page" not in request.query_params:
        legacy_limit = max(1, min(limit, 500 if include_all else 50))
        jobs_payload = list_jobs_for_user(auth.user_id, include_all=include_all, limit=legacy_limit)
        total_items = count_jobs_for_user(auth.user_id, include_all=include_all, max_items=None if include_all else 50)
        return {
            "jobs": jobs_payload,
            "pagination": {
                "page": 1,
                "page_size": page_size,
                "returned": len(jobs_payload),
                "total_items": total_items,
                "total_pages": max((total_items + page_size - 1) // page_size, 0),
                "max_items": None if include_all else 50,
                "is_admin": include_all,
            },
        }

    total_items = count_jobs_for_user(auth.user_id, include_all=include_all, max_items=None if include_all else 50)
    total_pages = max((total_items + page_size - 1) // page_size, 0)
    page = min(page, total_pages or 1)
    offset = (page - 1) * page_size
    fetch_limit = min(page_size * prefetch_pages, max(total_items - offset, 0) or page_size)
    jobs_payload = list_jobs_for_user(
        auth.user_id,
        include_all=include_all,
        limit=fetch_limit,
        offset=offset,
    )
    return {
        "jobs": jobs_payload,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "returned": len(jobs_payload),
            "total_items": total_items,
            "total_pages": total_pages,
            "max_items": None if include_all else 50,
            "is_admin": include_all,
        },
    }


@app.delete("/api/jobs/{job_id}")
async def delete_job_from_history(request: Request, job_id: str) -> dict:
    auth = require_token(request)
    if not db_enabled():
        raise HTTPException(status_code=503, detail="Database is not enabled.")

    if auth.role == "admin":
        if not admin_soft_delete_job(job_id, auth.user_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"ok": True, "mode": "admin_deleted"}

    if not hide_job_for_user(job_id, auth.user_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"ok": True, "mode": "hidden"}


@app.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict:
    auth = require_job_access(request, job_id)
    payload = public_job_payload(read_status(job_id))
    if db_enabled():
        payload["feedback"] = get_job_feedback(job_id, auth.user_id)
    return payload


@app.post("/api/jobs/{job_id}/feedback")
async def save_job_feedback(request: Request, job_id: str) -> dict:
    auth = require_job_access(request, job_id)
    if not db_enabled():
        raise HTTPException(status_code=503, detail="Database is not enabled.")
    if auth.role != "admin" and job_admin_deleted_at(job_id):
        raise HTTPException(status_code=410, detail="Job was deleted by administrator.")

    record = read_status(job_id)
    if record.status not in {"draft_ready", "completed"}:
        raise HTTPException(status_code=409, detail=f"Job is {record.status}.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {"qualified", "unqualified"}:
        raise HTTPException(status_code=400, detail="Feedback verdict must be qualified or unqualified.")
    note = str(payload.get("note") or "").strip()[:2000]

    try:
        feedback = upsert_job_feedback(job_id, auth.user_id, verdict, note, source="ui")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "quote feedback saved job_id=%s user_id=%s verdict=%s note_chars=%s",
        job_id,
        auth.user_id,
        verdict,
        len(note),
    )
    asyncio.create_task(sync_feedback_to_rag_safely(record, auth, feedback))
    return {"ok": True, "feedback": feedback, "rag": {"queued": True}}


@app.get("/api/jobs/{job_id}/report")
async def get_report(request: Request, job_id: str) -> PlainTextResponse:
    auth = require_job_access(request, job_id)
    if auth.role != "admin" and job_admin_deleted_at(job_id):
        raise HTTPException(status_code=410, detail="Job was deleted by administrator.")
    record = read_status(job_id)
    if record.status not in {"draft_ready", "completed"} or not record.report_path:
        raise HTTPException(status_code=409, detail=f"Job is {record.status}.")

    path = Path(record.report_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file not found.")

    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


@app.get("/api/jobs/{job_id}/excel")
async def get_excel(request: Request, job_id: str) -> FileResponse:
    auth = require_job_access(request, job_id)
    if auth.role != "admin" and job_admin_deleted_at(job_id):
        raise HTTPException(status_code=410, detail="Job was deleted by administrator.")
    record = read_status(job_id)
    if record.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {record.status}.")

    path = excel_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Excel cost table not found.")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{job_id}-cost-table.xlsx",
    )


@app.get("/api/jobs/{job_id}/assets")
async def get_assets(request: Request, job_id: str) -> dict:
    auth = require_job_access(request, job_id)
    if auth.role != "admin" and job_admin_deleted_at(job_id):
        raise HTTPException(status_code=410, detail="Job was deleted by administrator.")
    assets = read_assets_payload(job_id)
    return {"assets": public_assets(job_id, assets)}


@app.get("/api/jobs/{job_id}/assets/{asset_id}")
async def get_asset(request: Request, job_id: str, asset_id: int) -> FileResponse:
    auth = require_job_access(request, job_id)
    if auth.role != "admin" and job_admin_deleted_at(job_id):
        raise HTTPException(status_code=410, detail="Job was deleted by administrator.")
    assets = read_assets_payload(job_id)
    if asset_id < 0 or asset_id >= len(assets):
        raise HTTPException(status_code=404, detail="Asset not found.")

    asset = assets[asset_id]
    path = resolve_asset_path(job_id, asset)
    return FileResponse(
        path,
        media_type=str(asset.get("mime_type") or "application/octet-stream"),
        filename=path.name,
    )
