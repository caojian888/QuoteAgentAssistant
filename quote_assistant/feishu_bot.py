from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from .db import db_enabled, find_or_create_oauth_user
from .feishu_auth import feishu_app_id, feishu_openapi_base_url, get_app_access_token
from .report_delivery import markdown_to_feishu_post_lines, split_feishu_post_lines


@dataclass
class FeishuAttachment:
    message_id: str
    resource_key: str
    resource_type: str
    file_name: str
    mime_type: str = ""


@dataclass
class FeishuDownloadedFile:
    file_name: str
    content: bytes
    mime_type: str = ""


@dataclass
class FeishuBotAction:
    kind: str
    receive_id: str = ""
    receive_id_type: str = "chat_id"
    reply_text: str = ""
    prompt: str = ""
    attachments: list[FeishuAttachment] | None = None
    message_id: str = ""
    sender: dict[str, Any] | None = None


@dataclass
class PendingQuote:
    prompt: str
    created_at: float
    updated_at: float
    attachments: list[FeishuAttachment]


_PENDING_QUOTES: dict[str, PendingQuote] = {}
_SEEN_EVENTS: dict[str, float] = {}


QUOTE_START_PATTERN = re.compile(r"(开启|开始|新建|初始化).{0,8}(报价|核价|成本|quote)", re.IGNORECASE)
QUOTE_RUN_PATTERN = re.compile(r"(帮我)?(报个?价|报价|核价|开始计算|开始生成|quote)", re.IGNORECASE)
SUPPORTED_MESSAGE_TYPES = {"file", "image"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def feishu_bot_enabled() -> bool:
    return _env_bool("QUOTE_FEISHU_BOT_ENABLED", False)


def feishu_event_verification_token() -> str:
    return os.getenv("QUOTE_FEISHU_EVENT_VERIFICATION_TOKEN", "").strip()


def feishu_event_encrypt_key() -> str:
    return os.getenv("QUOTE_FEISHU_EVENT_ENCRYPT_KEY", "").strip()


def feishu_bot_default_prompt() -> str:
    return (
        os.getenv("QUOTE_FEISHU_BOT_DEFAULT_PROMPT", "").strip()
        or "请识别上传图纸的物料品类并生成报价报告，缺少的规格、数量或工艺参数列为待确认。"
    )


def feishu_bot_max_review_rounds() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_MAX_REVIEW_ROUNDS", 2), 0)


def feishu_bot_audit_enabled() -> bool:
    return _env_bool("QUOTE_FEISHU_BOT_AUDIT", False)


def feishu_bot_session_seconds() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_SESSION_SECONDS", 600), 60)


def feishu_bot_max_file_bytes() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_MAX_FILE_MB", 80), 1) * 1024 * 1024


def allowed_chat_ids() -> set[str]:
    raw = os.getenv("QUOTE_FEISHU_BOT_ALLOWED_CHAT_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def cleanup_feishu_bot_state(now: float | None = None) -> None:
    current = now or time.time()
    session_ttl = feishu_bot_session_seconds()
    for key, item in list(_PENDING_QUOTES.items()):
        if current - item.updated_at > session_ttl:
            _PENDING_QUOTES.pop(key, None)

    event_ttl = max(session_ttl, 1800)
    for key, seen_at in list(_SEEN_EVENTS.items()):
        if current - seen_at > event_ttl:
            _SEEN_EVENTS.pop(key, None)


def decode_feishu_event(raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Feishu event payload.") from exc

    if "encrypt" in payload:
        payload = decrypt_feishu_event(str(payload.get("encrypt") or ""))

    verify_feishu_signature(raw_body, headers)
    verify_feishu_event_token(payload)
    return payload


def decrypt_feishu_event(encrypted_text: str) -> dict[str, Any]:
    encrypt_key = feishu_event_encrypt_key()
    if not encrypt_key:
        raise HTTPException(status_code=400, detail="Feishu event is encrypted, but QUOTE_FEISHU_EVENT_ENCRYPT_KEY is not configured.")
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="pycryptodome is required for encrypted Feishu events.") from exc

    try:
        key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        encrypted = base64.b64decode(encrypted_text)
        if len(encrypted) <= AES.block_size:
            raise ValueError("encrypted payload is too short")
        iv = encrypted[: AES.block_size]
        cipher_text = encrypted[AES.block_size :]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(cipher_text)
        pad = plain[-1]
        if 1 <= pad <= AES.block_size:
            plain = plain[:-pad]
        plain_text = plain.decode("utf-8", errors="replace")
        start = plain_text.find("{")
        end = plain_text.rfind("}")
        if start >= 0 and end >= start:
            plain_text = plain_text[start : end + 1]
        return json.loads(plain_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt Feishu event payload.") from exc


def verify_feishu_signature(raw_body: bytes, headers: dict[str, str]) -> None:
    encrypt_key = feishu_event_encrypt_key()
    if not encrypt_key:
        return

    signature = headers.get("x-lark-signature") or headers.get("x-tt-signature") or ""
    timestamp = headers.get("x-lark-request-timestamp") or headers.get("x-tt-request-timestamp") or ""
    nonce = headers.get("x-lark-request-nonce") or headers.get("x-tt-request-nonce") or ""
    if not signature or not timestamp or not nonce:
        return

    body_text = raw_body.decode("utf-8", errors="replace")
    expected = hashlib.sha256(f"{timestamp}{nonce}{encrypt_key}{body_text}".encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid Feishu event signature.")


def verify_feishu_event_token(payload: dict[str, Any]) -> None:
    expected = feishu_event_verification_token()
    if not expected:
        return

    token = (
        str((payload.get("header") or {}).get("token") or "")
        or str((payload.get("event") or {}).get("token") or "")
        or str(payload.get("token") or "")
    )
    if not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid Feishu event token.")


def feishu_challenge_response(payload: dict[str, Any]) -> dict[str, str] | None:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    challenge = event.get("challenge") or payload.get("challenge")
    event_type = (payload.get("header") or {}).get("event_type") or event.get("type") or payload.get("type")
    if challenge and str(event_type) in {"url_verification", "endpoint.url_validation"}:
        return {"challenge": str(challenge)}
    if challenge and not event_type:
        return {"challenge": str(challenge)}
    return None


def feishu_event_id(payload: dict[str, Any]) -> str:
    return str((payload.get("header") or {}).get("event_id") or "")


def is_duplicate_feishu_event(payload: dict[str, Any]) -> bool:
    cleanup_feishu_bot_state()
    event_id = feishu_event_id(payload)
    if not event_id:
        return False
    if event_id in _SEEN_EVENTS:
        return True
    _SEEN_EVENTS[event_id] = time.time()
    return False


def build_feishu_bot_action(payload: dict[str, Any]) -> FeishuBotAction:
    cleanup_feishu_bot_state()
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    if header.get("event_type") != "im.message.receive_v1":
        return FeishuBotAction(kind="ignore")

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    if str(sender.get("sender_type") or "user") != "user":
        return FeishuBotAction(kind="ignore")

    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    chat_id = str(message.get("chat_id") or "")
    if not chat_id:
        return FeishuBotAction(kind="ignore")

    allowlist = allowed_chat_ids()
    if allowlist and chat_id not in allowlist:
        return FeishuBotAction(kind="ignore")

    message_id = str(message.get("message_id") or "")
    chat_type = str(message.get("chat_type") or "")
    message_type = str(message.get("message_type") or "")
    content = parse_message_content(message.get("content"))
    text = clean_feishu_message_text(str(content.get("text") or ""))
    conversation_key = feishu_conversation_key(message, sender)

    if message_type == "text":
        if has_quote_start_intent(text):
            prompt = build_quote_prompt(text)
            _PENDING_QUOTES[conversation_key] = PendingQuote(
                prompt=prompt,
                created_at=time.time(),
                updated_at=time.time(),
                attachments=[],
            )
            return FeishuBotAction(
                kind="reply",
                receive_id=chat_id,
                reply_text="已开启报价会话，请直接上传图纸、PDF 或图片。文件上传完后 @我发送“帮我报价”，我再开始识图报价。",
            )
        if has_quote_run_intent(text):
            pending = _PENDING_QUOTES.get(conversation_key)
            if not pending:
                return FeishuBotAction(
                    kind="reply",
                    receive_id=chat_id,
                    reply_text="还没有开启报价会话。请先 @我发送“开启报价”，然后上传图纸或 PDF。",
                )
            if not pending.attachments:
                return FeishuBotAction(
                    kind="reply",
                    receive_id=chat_id,
                    reply_text="报价会话已开启，但我还没有收到图纸文件。请继续上传 PDF、图片或常见图纸文件。",
                )
            _PENDING_QUOTES.pop(conversation_key, None)
            return FeishuBotAction(
                kind="create_job",
                receive_id=chat_id,
                prompt=pending.prompt,
                attachments=list(pending.attachments),
                sender=sender,
            )
        if chat_type == "p2p":
            return FeishuBotAction(
                kind="reply",
                receive_id=chat_id,
                reply_text="发送“开启报价”，然后上传图纸或 PDF；上传完后发送“帮我报价”，我会开始识图报价。",
            )
        return FeishuBotAction(kind="ignore")

    if message_type in SUPPORTED_MESSAGE_TYPES:
        pending = _PENDING_QUOTES.get(conversation_key)
        if not pending:
            if chat_type == "p2p":
                return FeishuBotAction(
                    kind="reply",
                    receive_id=chat_id,
                    reply_text="我收到了文件。请先发送“开启报价”，我会把后续上传的图纸放进同一个报价会话里。",
                )
            return FeishuBotAction(kind="ignore")

        attachment = attachment_from_message(message_type, content, message_id)
        if not attachment:
            return FeishuBotAction(
                kind="reply",
                receive_id=chat_id,
                reply_text="我收到了文件消息，但没有找到可下载的图纸资源。请重新上传 PDF、图片或常见图纸文件。",
            )

        pending.attachments.append(attachment)
        pending.updated_at = time.time()
        return FeishuBotAction(
            kind="reply",
            receive_id=chat_id,
            reply_text=f"已收到 {len(pending.attachments)} 个文件。继续上传，或 @我发送“帮我报价”开始报价。",
        )

    return FeishuBotAction(kind="ignore")


def parse_message_content(raw_content: object) -> dict[str, Any]:
    if isinstance(raw_content, dict):
        return raw_content
    if not isinstance(raw_content, str) or not raw_content.strip():
        return {}
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return {"text": raw_content}
    return parsed if isinstance(parsed, dict) else {}


def clean_feishu_message_text(text: str) -> str:
    cleaned = re.sub(r"<at\s+[^>]*>.*?</at>", " ", text)
    cleaned = re.sub(r"<at\s+[^>]*/>", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def has_quote_start_intent(text: str) -> bool:
    return bool(QUOTE_START_PATTERN.search(text or ""))


def has_quote_run_intent(text: str) -> bool:
    value = text or ""
    return bool(QUOTE_RUN_PATTERN.search(value)) and not has_quote_start_intent(value)


def build_quote_prompt(text: str) -> str:
    cleaned = clean_feishu_message_text(text)
    default = feishu_bot_default_prompt()
    if not cleaned or cleaned in {"帮我报价", "报价", "核价"}:
        return default
    return f"{default}\n\n用户补充需求：{cleaned}"


def feishu_conversation_key(message: dict[str, Any], sender: dict[str, Any]) -> str:
    chat_id = str(message.get("chat_id") or "")
    sender_id = sender_identity(sender) or "unknown"
    return f"{chat_id}:{sender_id}"


def sender_identity(sender: dict[str, Any]) -> str:
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    return str(sender_id.get("union_id") or sender_id.get("open_id") or sender_id.get("user_id") or "").strip()


def sender_profile(sender: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    provider_user_id = sender_identity(sender)
    profile = {
        "union_id": sender_id.get("union_id"),
        "open_id": sender_id.get("open_id"),
        "user_id": sender_id.get("user_id"),
        "tenant_key": sender.get("tenant_key"),
        "sender_type": sender.get("sender_type"),
    }
    return provider_user_id, {key: value for key, value in profile.items() if value}


def ensure_feishu_event_user(sender: dict[str, Any]) -> int | None:
    if not db_enabled():
        return None
    provider_user_id, profile = sender_profile(sender)
    if not provider_user_id:
        return None
    user = find_or_create_oauth_user("feishu", provider_user_id, profile)
    return int(user["id"]) if user and user.get("id") is not None else None


def attachment_from_message(message_type: str, content: dict[str, Any], message_id: str) -> FeishuAttachment | None:
    if message_type == "image":
        image_key = str(content.get("image_key") or "").strip()
        if not image_key:
            return None
        return FeishuAttachment(
            message_id=message_id,
            resource_key=image_key,
            resource_type="image",
            file_name=f"{image_key}.png",
            mime_type="image/png",
        )

    file_key = str(content.get("file_key") or "").strip()
    if not file_key:
        return None
    file_name = str(content.get("file_name") or content.get("name") or f"{file_key}.bin").strip()
    return FeishuAttachment(
        message_id=message_id,
        resource_key=file_key,
        resource_type="file",
        file_name=file_name,
        mime_type=str(content.get("mime_type") or ""),
    )


async def download_feishu_attachments(attachments: list[FeishuAttachment]) -> list[FeishuDownloadedFile]:
    if not attachments:
        return []

    token = await get_app_access_token()
    timeout = max(_env_int("QUOTE_FEISHU_BOT_TIMEOUT_SECONDS", 30), 5)
    max_bytes = feishu_bot_max_file_bytes()
    downloaded: list[FeishuDownloadedFile] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attachment in attachments:
            if not attachment.message_id:
                raise RuntimeError("Feishu message_id is missing, cannot download attachments.")
            url = (
                f"{feishu_openapi_base_url()}/open-apis/im/v1/messages/"
                f"{attachment.message_id}/resources/{attachment.resource_key}"
            )
            response = await client.get(
                url,
                params={"type": attachment.resource_type},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type.lower():
                try:
                    data = response.json()
                except Exception:
                    data = {}
                code = data.get("code") if isinstance(data, dict) else None
                if code not in {0, "0", None}:
                    raise RuntimeError(str(data.get("msg") or data.get("message") or "Feishu attachment download failed."))
            content = response.content
            if len(content) > max_bytes:
                raise RuntimeError(f"文件 {attachment.file_name} 超过 {max_bytes // 1024 // 1024} MB 限制。")
            downloaded.append(
                FeishuDownloadedFile(
                    file_name=attachment.file_name,
                    content=content,
                    mime_type=attachment.mime_type or response.headers.get("content-type", ""),
                )
            )
    return downloaded


async def send_feishu_text(receive_id: str, text: str, receive_id_type: str = "chat_id") -> None:
    if not receive_id or not text:
        return
    token = await get_app_access_token()
    timeout = max(_env_int("QUOTE_FEISHU_BOT_TIMEOUT_SECONDS", 30), 5)
    url = f"{feishu_openapi_base_url()}/open-apis/im/v1/messages"
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        code = data.get("code", 0)
        if code not in {0, "0", None}:
            raise RuntimeError(str(data.get("msg") or data.get("message") or "Feishu send message failed."))


async def send_feishu_post(
    receive_id: str,
    title: str,
    content_lines: list[list[dict[str, Any]]],
    receive_id_type: str = "chat_id",
) -> None:
    if not receive_id or not content_lines:
        return
    token = await get_app_access_token()
    timeout = max(_env_int("QUOTE_FEISHU_BOT_TIMEOUT_SECONDS", 30), 5)
    url = f"{feishu_openapi_base_url()}/open-apis/im/v1/messages"
    payload = {
        "receive_id": receive_id,
        "msg_type": "post",
        "content": json.dumps(
            {
                "zh_cn": {
                    "title": title,
                    "content": content_lines,
                }
            },
            ensure_ascii=False,
        ),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        code = data.get("code", 0)
        if code not in {0, "0", None}:
            raise RuntimeError(str(data.get("msg") or data.get("message") or "Feishu send post message failed."))


async def send_feishu_file(receive_id: str, path: Path, receive_id_type: str = "chat_id") -> None:
    if not receive_id:
        return
    file_key = await upload_feishu_file(path)
    await send_feishu_file_message(receive_id, file_key, receive_id_type=receive_id_type)


async def upload_feishu_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Feishu upload file not found: {path}")

    token = await get_app_access_token()
    timeout = max(_env_int("QUOTE_FEISHU_BOT_TIMEOUT_SECONDS", 30), 5)
    url = f"{feishu_openapi_base_url()}/open-apis/im/v1/files"
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("Feishu upload file is empty.")
    if size > feishu_bot_max_file_bytes():
        raise RuntimeError(f"文件 {path.name} 超过 {feishu_bot_max_file_bytes() // 1024 // 1024} MB 限制。")

    data = {
        "file_type": feishu_file_type(path),
        "file_name": path.name,
    }
    with path.open("rb") as file_obj:
        files = {"file": (path.name, file_obj, "application/octet-stream")}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                data=data,
                files=files,
            )
    response.raise_for_status()
    payload = response.json()
    code = payload.get("code", 0)
    if code not in {0, "0", None}:
        raise RuntimeError(str(payload.get("msg") or payload.get("message") or "Feishu upload file failed."))
    file_key = ((payload.get("data") or {}).get("file_key") or payload.get("file_key") or "").strip()
    if not file_key:
        raise RuntimeError("Feishu upload did not return file_key.")
    return file_key


def feishu_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        return "xls"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".doc", ".docx"}:
        return "doc"
    if suffix in {".ppt", ".pptx"}:
        return "ppt"
    return "stream"


async def send_feishu_file_message(receive_id: str, file_key: str, receive_id_type: str = "chat_id") -> None:
    if not receive_id or not file_key:
        return
    token = await get_app_access_token()
    timeout = max(_env_int("QUOTE_FEISHU_BOT_TIMEOUT_SECONDS", 30), 5)
    url = f"{feishu_openapi_base_url()}/open-apis/im/v1/messages"
    payload = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        code = data.get("code", 0)
        if code not in {0, "0", None}:
            raise RuntimeError(str(data.get("msg") or data.get("message") or "Feishu send file message failed."))


async def send_feishu_report_messages(
    receive_id: str,
    job_id: str,
    report_text: str,
    receive_id_type: str = "chat_id",
) -> None:
    title = f"报价任务 {feishu_job_code(job_id)} 已完成。"
    excerpt = compact_report_excerpt(report_text, feishu_report_max_chars())
    lines = markdown_to_feishu_post_lines(excerpt)
    chunks = split_feishu_post_lines(
        lines,
        max_chars=feishu_post_max_chars(),
        max_lines=feishu_post_max_lines(),
    )
    if not chunks:
        await send_feishu_text(receive_id, f"{title}\n\n报告已生成，但正文为空。", receive_id_type)
        return

    for index, chunk in enumerate(chunks, start=1):
        message_title = title if len(chunks) == 1 else f"{title}（{index}/{len(chunks)}）"
        await send_feishu_post(receive_id, message_title, chunk, receive_id_type)


def feishu_report_max_chars() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_REPORT_MAX_CHARS", 24000), 1000)


def feishu_post_max_chars() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_POST_MAX_CHARS", 5200), 1000)


def feishu_post_max_lines() -> int:
    return max(_env_int("QUOTE_FEISHU_BOT_POST_MAX_LINES", 90), 20)


def split_feishu_text(text: str, chunk_size: int | None = None) -> list[str]:
    chunk_size = chunk_size or max(_env_int("QUOTE_FEISHU_BOT_MESSAGE_CHARS", 1800), 500)
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    while cleaned:
        if len(cleaned) <= chunk_size:
            chunks.append(cleaned)
            break
        cut = cleaned.rfind("\n\n", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = cleaned.rfind("\n", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = chunk_size
        chunks.append(cleaned[:cut].strip())
        cleaned = cleaned[cut:].strip()
    return [item for item in chunks if item]


def feishu_job_code(job_id: str, created_at: str = "") -> str:
    date_text = time.strftime("%Y%m%d")
    return f"QA-{date_text}-{job_id[:8]}"


def quote_created_text(job_id: str, created_at: str = "") -> str:
    return f"已创建任务 {feishu_job_code(job_id, created_at)}，正在识图报价。完成后我会把报告正文、PDF 和 Excel 发到当前飞书会话。"


def quote_failed_text(job_id: str, error: str = "") -> str:
    detail = f"\n错误：{error}" if error else ""
    return f"报价任务 {feishu_job_code(job_id)} 执行失败，请稍后重试或到工作台查看。{detail}"


def quote_completed_text(
    job_id: str,
    report_text: str,
    base_url: str,
    *,
    has_excel: bool = False,
    status: str = "completed",
    error: str = "",
) -> str:
    if status == "failed":
        return quote_failed_text(job_id, error)

    excerpt = compact_report_excerpt(report_text, 1200)
    fallback = ""
    if base_url:
        report_url = f"{base_url.rstrip('/')}/jobs/{job_id}/report"
        excel_line = f"\nExcel 备用链接：{base_url.rstrip('/')}/jobs/{job_id}/excel" if has_excel else ""
        fallback = f"\n\n完整报告备用链接：{report_url}{excel_line}"
    return f"报价任务 {feishu_job_code(job_id)} 已完成。\n\n{excerpt}{fallback}"


def compact_report_excerpt(text: str, max_chars: int = 1200) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not cleaned:
        return "报告已生成，请打开完整报告查看。"
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "\n..."


def feishu_bot_config_status() -> dict[str, Any]:
    has_verification_token = bool(feishu_event_verification_token())
    has_encrypt_key = bool(feishu_event_encrypt_key())
    return {
        "enabled": feishu_bot_enabled(),
        "app_id_configured": bool(feishu_app_id()),
        "verification_token_configured": has_verification_token,
        "encrypt_key_configured": has_encrypt_key,
        "event_security_configured": has_verification_token or has_encrypt_key,
    }
