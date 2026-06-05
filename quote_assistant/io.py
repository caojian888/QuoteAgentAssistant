from __future__ import annotations

import base64
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI

from .base_url import normalize_base_url


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def is_pdf(path: Path) -> bool:
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    return mime_type == "application/pdf" or path.suffix.lower() == ".pdf"


def render_pdf_pages(path: Path) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF support requires PyMuPDF. Run `pip install -r requirements.txt`.") from exc

    dpi = max(env_int("QUOTE_PDF_RENDER_DPI", 200), 72)
    max_pages = max(env_int("QUOTE_PDF_MAX_PAGES", 20), 1)
    default_workers = min(max(os.cpu_count() or 2, 1), 4)
    workers = max(env_int("QUOTE_PDF_RENDER_WORKERS", default_workers), 1)
    output_dir = path.parent / "_pdf_pages" / path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(path) as document:
        page_count = min(document.page_count, max_pages)

    def render_one(index: int) -> Path:
        image_path = output_dir / f"page-{index + 1:03d}.png"
        if image_path.exists():
            return image_path

        # Open the document inside the worker; sharing a fitz.Document across
        # threads is not guaranteed to be safe.
        with fitz.open(path) as worker_document:
            page = worker_document.load_page(index)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(image_path)
        return image_path

    if page_count <= 0:
        rendered: list[Path] = []
    elif workers == 1 or page_count == 1:
        rendered = [render_one(index) for index in range(page_count)]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, page_count)) as executor:
            rendered = list(executor.map(render_one, range(page_count)))

    if not rendered:
        raise RuntimeError(f"PDF has no pages: {path.name}")
    return rendered


def encode_image(path: Path) -> dict[str, str]:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime_type};base64,{data}"}


def upload_file(path: Path, client: OpenAI) -> dict[str, str]:
    with path.open("rb") as file_obj:
        uploaded = client.files.create(file=file_obj, purpose="user_data")
    return {"type": "input_file", "file_id": uploaded.id}


def file_client() -> OpenAI:
    api_key = (
        os.getenv("QUOTE_FILE_API_KEY")
        or os.getenv("QUOTE_VISION_API_KEY")
        or os.getenv("QUOTE_REVIEW_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("QUOTE_FILE_BASE_URL")
        or os.getenv("QUOTE_VISION_BASE_URL")
        or os.getenv("QUOTE_REVIEW_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
    )
    if base_url:
        return OpenAI(
            api_key=api_key,
            base_url=normalize_base_url(base_url),
            timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0),
        )
    return OpenAI(api_key=api_key, timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0))


def build_agent_input(prompt: str, files: list[Path]) -> str | list[dict[str, Any]]:
    if not files:
        return prompt

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    upload_client: OpenAI | None = None

    for path in files:
        if not path.exists():
            raise FileNotFoundError(path)

        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if is_pdf(path):
            for image_path in render_pdf_pages(path):
                content.append(encode_image(image_path))
        elif mime_type.startswith("image/"):
            content.append(encode_image(path))
        else:
            if upload_client is None:
                upload_client = file_client()
            content.append(upload_file(path, upload_client))

    return [{"role": "user", "content": content}]
