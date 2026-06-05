from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

try:
    from PIL import Image as PILImage
    from PIL import ImageOps
except ImportError:  # pragma: no cover - validated at runtime.
    PILImage = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


logger = logging.getLogger("uvicorn.error")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "drawing"


def _asset_source_label(asset: dict[str, Any], path: Path) -> str:
    return str(asset.get("source") or asset.get("label") or path.stem)


def _crop_digest(source_path: Path, region_name: str) -> str:
    raw = f"{source_path}|{region_name}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:10]


def _threshold_bbox(image: Any) -> tuple[int, int, int, int] | None:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < 245 else 0, "L")
    return mask.getbbox()


def _clamp_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = bbox
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    if right - left < 80 or bottom - top < 80:
        return None
    return left, top, right, bottom


def _inset_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    ratio: float,
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = bbox
    inset_x = int(width * ratio)
    inset_y = int(height * ratio)
    return _clamp_bbox((left + inset_x, top + inset_y, right - inset_x, bottom - inset_y), width, height)


def _main_drawing_bbox(image: Any) -> tuple[int, int, int, int] | None:
    width, height = image.size
    content_bbox = _threshold_bbox(image) or (0, 0, width, height)
    content_bbox = _inset_bbox(content_bbox, width, height, 0.006) or content_bbox

    left, top, right, bottom = content_bbox
    content_width = right - left
    content_height = bottom - top
    if content_width <= 0 or content_height <= 0:
        return None

    # Landscape engineering drawings often carry title blocks and notes on the
    # right/bottom. Keep the main drawing views prominent for the Excel thumbnail,
    # but do not cut so aggressively that lower projection views disappear.
    if width >= height * 1.15:
        candidate = (
            left,
            top,
            left + int(content_width * 0.88),
            top + int(content_height * 0.88),
        )
        return _clamp_bbox(candidate, width, height) or content_bbox

    candidate = (
        left,
        top,
        right,
        top + int(content_height * 0.82),
    )
    return _clamp_bbox(candidate, width, height) or content_bbox


def _save_crop(source_path: Path, target_path: Path, bbox: tuple[int, int, int, int]) -> Path:
    if PILImage is None or ImageOps is None:
        raise RuntimeError("Drawing region crops require Pillow.")

    if target_path.exists() and target_path.stat().st_mtime >= source_path.stat().st_mtime:
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with PILImage.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        crop = image.crop(bbox)
        max_edge = max(_env_int("QUOTE_EXCEL_IMAGE_REGION_MAX_EDGE", 1800), 600)
        if max(crop.size) > max_edge:
            crop.thumbnail((max_edge, max_edge))
        if crop.mode in {"RGBA", "LA"}:
            background = PILImage.new("RGB", crop.size, "white")
            background.paste(crop.convert("RGBA"), mask=crop.getchannel("A"))
            crop = background
        elif crop.mode != "RGB":
            crop = crop.convert("RGB")
        crop.save(target_path, "PNG", optimize=True)
    return target_path


def create_excel_drawing_region_assets(
    image_assets: list[dict[str, Any]] | None,
    output_dir: Path,
) -> list[dict[str, Any]]:
    if not _env_bool("QUOTE_EXCEL_IMAGE_REGIONS_ENABLED", True):
        return []
    if PILImage is None or ImageOps is None:
        logger.warning("quote excel drawing regions skipped: Pillow is unavailable")
        return []

    max_assets = max(_env_int("QUOTE_EXCEL_IMAGE_REGION_MAX_ASSETS", 40), 1)
    region_dir = output_dir / "_drawing_regions"
    regions: list[dict[str, Any]] = []

    for asset in image_assets or []:
        if len(regions) >= max_assets:
            break
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        if asset.get("kind") == "drawing_region":
            continue

        source_path = Path(str(asset.get("path") or ""))
        if not source_path.exists():
            continue

        try:
            with PILImage.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                bbox = _main_drawing_bbox(image)
        except Exception as exc:
            logger.warning("quote excel drawing region crop failed path=%s error=%s", source_path, exc)
            continue

        if bbox is None:
            continue

        label = str(asset.get("label") or source_path.name)
        source_label = _asset_source_label(asset, source_path)
        target_name = (
            f"{_safe_stem(source_label)}-{_safe_stem(source_path.stem)}"
            f"-drawing-area-{_crop_digest(source_path, 'drawing-area')}.png"
        )
        target_path = region_dir / target_name

        try:
            _save_crop(source_path, target_path, bbox)
        except Exception as exc:
            logger.warning("quote excel drawing region save failed path=%s error=%s", source_path, exc)
            continue

        regions.append(
            {
                "kind": "drawing_region",
                "type": "image",
                "label": f"{label} drawing area",
                "source": source_label,
                "page": asset.get("page"),
                "path": str(target_path),
                "mime_type": "image/png",
                "derived_from": str(source_path),
                "region": "drawing_area",
                "bbox": list(bbox),
            }
        )

    if regions:
        logger.info(
            "quote excel drawing regions ready regions=%s source_assets=%s",
            len(regions),
            len(image_assets or []),
        )
    return regions
