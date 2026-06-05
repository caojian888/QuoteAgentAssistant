from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from .io import is_pdf, render_pdf_pages


logger = logging.getLogger("uvicorn.error")


def is_image(path: Path) -> bool:
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    return mime_type.startswith("image/")


def asset_label(path: Path) -> str:
    if path.parent.name and path.parent.parent.name == "_pdf_pages":
        return f"{display_upload_name(path.parent.name)} / {path.stem}"
    return display_upload_name(path.name)


def display_upload_name(name: str) -> str:
    return re.sub(r"^\d{2}-", "", name)


def prepare_attachments(files: list[Path]) -> dict[str, Any]:
    vision_files: list[Path] = []
    assets: list[dict[str, Any]] = []
    report_lines: list[str] = []

    for path in files:
        if not path.exists():
            raise FileNotFoundError(path)

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if is_pdf(path):
            pages = render_pdf_pages(path)
            display_name = display_upload_name(path.name)
            assets.append(
                {
                    "kind": "source",
                    "type": "pdf",
                    "label": display_name,
                    "path": str(path),
                    "mime_type": "application/pdf",
                }
            )
            report_lines.append(f"- {display_name}: PDF rendered to {len(pages)} PNG page image(s).")
            for index, page_path in enumerate(pages, start=1):
                vision_files.append(page_path)
                assets.append(
                    {
                        "kind": "rendered_page",
                        "type": "image",
                        "label": f"{display_name} page {index}",
                        "source": display_name,
                        "page": index,
                        "path": str(page_path),
                        "mime_type": "image/png",
                    }
                )
        elif is_image(path):
            vision_files.append(path)
            assets.append(
                {
                    "kind": "source",
                    "type": "image",
                    "label": asset_label(path),
                    "path": str(path),
                    "mime_type": mime_type,
                }
            )
            report_lines.append(f"- {display_upload_name(path.name)}: image used directly.")
        else:
            display_name = display_upload_name(path.name)
            vision_files.append(path)
            assets.append(
                {
                    "kind": "source",
                    "type": "file",
                    "label": display_name,
                    "path": str(path),
                    "mime_type": mime_type,
                }
            )
            report_lines.append(f"- {display_name}: non-image file kept as uploaded.")

    logger.info(
        "quote attachments prepared original_files=%s vision_files=%s assets=%s",
        len(files),
        len(vision_files),
        len(assets),
    )
    return {
        "original_files": files,
        "vision_files": vision_files,
        "assets": assets,
        "preprocess_report": "\n".join(report_lines),
    }
