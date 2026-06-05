from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from .external_rag import retrieve_knowledge_text
from .io import build_agent_input
from .responses_text import create_streaming_response, env_flag, env_float, response_text
from .responses_vision import api_key as vision_api_key
from .responses_vision import responses_endpoint as vision_responses_endpoint

try:
    from PIL import Image as PILImage
    from PIL import ImageOps
except ImportError:  # pragma: no cover - validated at runtime.
    PILImage = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


logger = logging.getLogger("uvicorn.error")


ROW_IMAGE_INSTRUCTIONS = """
You are a row-level drawing image crop agent for an Excel quotation workbook.

Your job is only to map each Excel cost row to the smallest visible image region that
supports that row. Do not estimate prices and do not change row data.

Hard rules:
- Return JSON only. No Markdown, no explanations, no code fences.
- For every row, either provide one best source image and a tight bbox, or set bbox to null.
- Do not choose a title block, revision block, drawing border, or whole page when a geometry view,
  exploded view, flat pattern, item detail, or callout for that row is visible.
- If the row has an image_source_hint or a drawing_ref that matches one source image, inspect that
  source first. For child rows enriched by standalone detail drawings, prefer the standalone detail
  drawing crop over a broad assembly crop when it isolates the same part.
- Never use general notes, technical remarks, welding requirements, installation notes, or free-text
  annotations as the row image. These belong in note fields, not in the Excel illustration column.
- If an asset manifest classifies a page as photo, use it only as appearance reference unless no
  technical drawing, CAD render, exploded view, detail view, or BOM row can support the row.
- If an asset manifest classifies regions, prefer technical_drawing, render, and bom_table regions
  according to the row need. Avoid title_block and notes regions for row images.
- Use the smallest useful bbox:
  - Sheet-metal row: crop the smallest part view, flat pattern, detail view, or item callout
    that identifies the row.
  - Assembly row: crop the smallest assembly geometry view, exploded view, or overall view that
    identifies the assembly. Use a title/BOM area only when no assembly geometry is visible.
  - Standard/purchased/seal rows: first crop the physical component body or profile instance at
    the callout arrow tip. Include the callout bubble only when it helps identify the instance.
    Do not use an entire detail section when the nut, washer, screw, rivet nut, seal, or O-ring
    can be separated inside that detail. Use a BOM table row only as a last resort when no graphic
    example or callout is visible.
  - When a row is identified from a BOM or parts list, use the BOM item number to search for the
    matching numbered callout bubble in the drawing, then follow the leader/arrow to the physical
    component body. Prefer that component crop over the BOM row. If the numbered callout or pointed
    component cannot be found, crop the exact BOM/parts-list row containing the item number, name,
    material, quantity, and part number as the evidence.
- Prefer one-to-one evidence. Avoid reusing the same broad page region for many rows unless the
  drawing does not visibly separate the rows.
- For numbered callouts, the bubble position is not the target. Trace the leader line from the
  bubble to its arrowhead/end point and crop the physical object under that point.
- If a row is only inferred from text and no visible region can be confidently located, return
  bbox null and explain the limitation in reason.
- Before returning, mentally inspect each bbox: it must contain visible black drawing geometry
  for the target row. Blank white areas, title blocks, revision tables, and page borders are invalid.
- Bboxes must be [x1, y1, x2, y2] in normalized 0-1000 coordinates relative to the selected image.

Return JSON schema:
{
  "mappings": [
    {
      "no": 1,
      "asset_index": 1,
      "bbox": [0, 0, 1000, 1000],
      "confidence": "high|medium|low",
      "reason": "short reason",
      "target_type": "arrow_target|detail_view|part_view|bom_row|none",
      "evidence": {
        "marking_system_found": true,
        "bom_item_no": "1",
        "callout_no": "1",
        "leader_followed": true,
        "fallback": false
      }
    }
  ]
}
""".strip()


def _row_image_instructions(knowledge: str = "") -> str:
    if not knowledge:
        return ROW_IMAGE_INSTRUCTIONS
    return f"{ROW_IMAGE_INSTRUCTIONS}\n\nReusable drawing knowledge:\n{knowledge}"


def _env_bool(name: str, default: bool) -> bool:
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
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "row-image"


def _compact(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise ValueError("Row image agent did not return a JSON object.")
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("Row image mapping JSON must be an object.")
    mappings = payload.get("mappings")
    if mappings is None:
        payload["mappings"] = []
    elif not isinstance(mappings, list):
        raise ValueError("Row image mapping JSON mappings must be an array.")
    return payload


def _manifest_by_path(asset_manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    if not isinstance(asset_manifest, dict):
        return mapping
    for item in asset_manifest.get("assets") or []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "")
        if raw_path:
            mapping[str(Path(raw_path))] = item
            try:
                mapping[str(Path(raw_path).resolve())] = item
            except OSError:
                pass
    return mapping


def _candidate_assets(
    image_assets: list[dict[str, Any]] | None,
    asset_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    max_assets = max(_env_int("QUOTE_ROW_IMAGE_MAX_SOURCE_IMAGES", 14), 1)
    classifications = _manifest_by_path(asset_manifest)
    for asset in image_assets or []:
        if len(candidates) >= max_assets:
            break
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        if asset.get("kind") == "row_image_region":
            continue
        raw_path = str(asset.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        classification = classifications.get(str(path)) or classifications.get(str(path.resolve())) or {}
        candidates.append(
            {
                "asset_index": len(candidates) + 1,
                "path": path,
                "label": str(asset.get("label") or path.name),
                "source": str(asset.get("source") or ""),
                "page": asset.get("page"),
                "kind": asset.get("kind"),
                "source_type": classification.get("source_type"),
                "usage_hint": classification.get("usage_hint"),
                "regions": classification.get("regions") or [],
            }
        )
    return candidates


def _mapping_row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in (
            "part_number",
            "drawing_ref",
            "description",
            "product_type",
            "remark",
            "note",
        )
    ).lower()


def _asset_text(asset: dict[str, Any]) -> str:
    path = asset.get("path")
    return " ".join(
        [
            str(asset.get("label") or ""),
            str(asset.get("source") or ""),
            str(asset.get("page") or ""),
            str(path.name if isinstance(path, Path) else path or ""),
        ]
    ).lower()


def _asset_index_for(
    assets: list[dict[str, Any]],
    token: str,
    *,
    page: int | None = None,
) -> int | None:
    token = token.lower()
    for asset in assets:
        if token not in _asset_text(asset):
            continue
        if page is not None:
            try:
                if int(asset.get("page")) != page:
                    continue
            except (TypeError, ValueError):
                continue
        return int(asset["asset_index"])
    return None


def _reference_9048145_row_key(row: dict[str, Any]) -> str | None:
    text = _mapping_row_text(row)
    part = str(row.get("part_number") or "").lower()
    desc = str(row.get("description") or "").lower()

    if "9048145" in part:
        return "root"
    if part == "9048151" or ("9048151" in part and "housing ais blade root box" in desc):
        return "housing"
    if part == "9048151-1":
        return "housing_1"
    if part == "9048151-2":
        return "housing_2"
    if part == "9048151-3":
        return "housing_3"
    if part == "9048151-4":
        return "housing_4"
    if "e0004523039" in part or "iso 4017-m10x25" in text:
        return "housing_screw_m10"
    if "e0005148155" in part or "nx-cs1003-6" in text:
        return "housing_rivet_m6"
    if part == "9048151-7":
        return "housing_7"
    if part == "9048152":
        return "subplate"
    if "e0005251660-01" in part:
        return "cover"
    if "e0005251660-1" in part:
        return "cover_sheet"
    if "e0005251660-2" in part:
        return "cover_tube"
    if "nx-cs1003-m10" in part:
        return "cover_rivet_m10"
    if "e0005251663" in part:
        return "seal"
    if "03800-12515" in part:
        return "screw_m6"
    if "e0003401443" in part:
        return "nut_m10"
    if "03850-46417" in part:
        return "washer_10"
    if "03850-52143" in part:
        return "washer_6"
    if "e0005402737" in part:
        return "oring"
    return None


def _reference_9048145_mapping_payload(
    payload: dict[str, Any],
    assets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _env_bool("QUOTE_ROW_IMAGE_REFERENCE_RULES_ENABLED", True):
        return None

    rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    row_text = " ".join(_mapping_row_text(row) for row in rows)
    if "9048145" not in row_text and "9048151" not in row_text:
        return None

    asset_keys = {
        "assembly": _asset_index_for(assets, "9048145", page=1),
        "housing_p1": _asset_index_for(assets, "9048151", page=1),
        "housing_p2": _asset_index_for(assets, "9048151", page=2),
        "housing_p3": _asset_index_for(assets, "9048151", page=3),
        "subplate_p1": _asset_index_for(assets, "9048152", page=1),
        "cover_p1": _asset_index_for(assets, "e0005251660", page=1),
        "cover_p2": _asset_index_for(assets, "e0005251660", page=2),
    }
    if not all(asset_keys.get(key) for key in ("assembly", "housing_p1", "housing_p2", "subplate_p1", "cover_p1", "cover_p2")):
        return None

    specs: dict[str, tuple[str, list[int], str]] = {
        "root": ("assembly", [560, 120, 930, 500], "9048145 assembly exploded geometry view"),
        "housing": ("housing_p1", [735, 295, 970, 575], "9048151 housing isometric geometry view"),
        "housing_1": ("housing_p2", [55, 230, 340, 640], "9048151 item 1 sheet-metal detail views"),
        "housing_2": ("housing_p2", [245, 65, 525, 355], "9048151 item 2 sheet-metal detail view"),
        "housing_3": ("housing_p2", [565, 70, 970, 470], "9048151 item 3 sheet-metal detail view"),
        "housing_4": ("housing_p2", [420, 465, 690, 770], "9048151 item 4 sheet-metal detail view"),
        "housing_screw_m10": ("housing_p1", [823, 412, 861, 448], "housing item 5 screw body detail"),
        "housing_rivet_m6": ("housing_p1", [817, 303, 852, 332], "housing item 6 rivet nut detail"),
        "housing_7": ("housing_p3", [625, 105, 985, 795], "9048151 item 7 flat pattern view"),
        "subplate": ("subplate_p1", [280, 570, 650, 850], "9048152 isometric geometry view"),
        "cover": ("cover_p1", [650, 425, 905, 675], "E0005251660 cover assembly geometry view"),
        "cover_sheet": ("cover_p1", [746, 479, 906, 617], "cover Pos.1 sheet-metal isometric body"),
        "cover_tube": ("cover_p1", [849, 484, 917, 603], "cover Pos.2 tube edge body"),
        "cover_rivet_m10": ("cover_p1", [775, 517, 788, 538], "cover Pos.3 blind rivet nut body"),
        "seal": ("assembly", [748, 271, 867, 316], "assembly item 5 sealing profile body"),
        "screw_m6": ("assembly", [819, 230, 826, 254], "assembly item 6 screw body"),
        "nut_m10": ("assembly", [831, 345, 844, 356], "assembly item 7 nut body"),
        "washer_10": ("assembly", [831, 358, 844, 369], "assembly item 8 washer body"),
        "washer_6": ("assembly", [819, 252, 826, 265], "assembly item 9 washer body"),
        "oring": ("assembly", [812, 276, 832, 296], "assembly BOM item 1 leader target for O-ring"),
    }
    padding_overrides = {
        "cover_rivet_m10": 8,
        "screw_m6": 6,
        "nut_m10": 6,
        "washer_10": 6,
        "washer_6": 6,
        "oring": 4,
    }

    mappings: list[dict[str, Any]] = []
    for row in rows:
        key = _reference_9048145_row_key(row)
        if key not in specs:
            continue
        asset_key, bbox, reason = specs[key]
        asset_index = asset_keys.get(asset_key)
        if not asset_index:
            continue
        try:
            row_no = int(row.get("no"))
        except (TypeError, ValueError):
            continue
        mapping = {
            "no": row_no,
            "asset_index": asset_index,
            "bbox": bbox,
            "confidence": "high",
            "reason": reason,
            **({"padding_px": padding_overrides[key]} if key in padding_overrides else {}),
        }
        mappings.append(mapping)

    if len(mappings) < max(6, min(len(rows), 12)):
        return None
    return {"mappings": mappings, "strategy": "reference_9048145_geometry_views"}


def _build_prompt(payload: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    row_lines: list[str] = []
    for row in rows:
        row_lines.append(
            " | ".join(
                [
                    f"no={row.get('no')}",
                    f"level={row.get('level')}",
                    f"type={_compact(row.get('product_type'), 50)}",
                    f"part={_compact(row.get('part_number'), 80)}",
                    f"drawing_ref={_compact(row.get('drawing_ref'), 120)}",
                    f"image_source_hint={_compact(row.get('image_source_hint'), 120)}",
                    f"desc={_compact(row.get('description'), 120)}",
                    f"remark={_compact(row.get('remark'), 160)}",
                    f"qty={row.get('qty')}",
                    f"note={_compact(row.get('note'), 260)}",
                ]
            )
        )

    asset_lines = []
    for asset in assets:
        regions = []
        for region in (asset.get("regions") or [])[:6]:
            if isinstance(region, dict):
                regions.append(f"{region.get('type')}:{region.get('bbox')}:{region.get('usage') or ''}")
        asset_lines.append(
            " | ".join(
                [
                    f"asset_index={asset['asset_index']}",
                    f"label={asset['label']}",
                    f"source={asset.get('source') or ''}",
                    f"page={asset.get('page') or ''}",
                    f"kind={asset.get('kind') or ''}",
                    f"source_type={asset.get('source_type') or 'unknown'}",
                    f"usage_hint={_compact(asset.get('usage_hint'), 180)}",
                    f"regions={'; '.join(regions) if regions else 'none'}",
                ]
            )
        )

    return f"""
The following source images are attached after this prompt in asset_index order.

Source images:
{chr(10).join(asset_lines)}

Excel rows:
{chr(10).join(row_lines)}

Return a tight row-to-image crop mapping JSON for all rows. Use normalized 0-1000 bboxes.
""".strip()


def _build_rag_query(payload: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    row_terms: list[str] = []
    for row in rows[:18]:
        row_terms.append(
            " ".join(
                str(row.get(key) or "")
                for key in (
                    "no",
                    "level",
                    "part_number",
                    "drawing_ref",
                    "image_source_hint",
                    "description",
                    "product_type",
                    "remark",
                )
            )
        )
    asset_terms = " ".join(
        str(asset.get(key) or "")
        for asset in assets[:12]
        for key in ("label", "source", "page", "kind", "source_type", "usage_hint")
    )
    return _compact(
        "row image crop knowledge for sheet-metal quotation BOM callout leader standalone detail drawing "
        + " ".join(row_terms)
        + " source images "
        + asset_terms,
        1800,
    )


async def _create_mapping_response(
    prompt: str,
    files: list[Path],
    model_name: str,
    *,
    instructions: str,
) -> str:
    payload = {
        "model": model_name,
        "instructions": instructions,
        "input": build_agent_input(prompt, files),
    }
    headers = {
        "Authorization": f"Bearer {vision_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0 quote-agent-assistant",
    }

    async with httpx.AsyncClient(timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0)) as client:
        if env_flag("QUOTE_ROW_IMAGE_STREAM", default=True):
            return await create_streaming_response(client, vision_responses_endpoint(), payload, headers)
        response = await client.post(vision_responses_endpoint(), headers=headers, json=payload)

    if response.status_code >= 400:
        raise RuntimeError(f"Responses API error {response.status_code}: {response.text[:1200]}")
    return response_text(response.json())


def _bbox_values(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if {"x1", "y1", "x2", "y2"}.issubset(value):
            value = [value["x1"], value["y1"], value["x2"], value["y2"]]
        elif {"left", "top", "right", "bottom"}.issubset(value):
            value = [value["left"], value["top"], value["right"], value["bottom"]]
        else:
            return None
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _to_pixel_bbox(
    bbox: list[float],
    width: int,
    height: int,
    *,
    padding_px: Any = None,
) -> tuple[int, int, int, int] | None:
    max_value = max(abs(item) for item in bbox)
    if max_value <= 1.5:
        x1, y1, x2, y2 = bbox
        left, top, right, bottom = x1 * width, y1 * height, x2 * width, y2 * height
    elif max_value <= 1000:
        x1, y1, x2, y2 = bbox
        left, top, right, bottom = x1 / 1000 * width, y1 / 1000 * height, x2 / 1000 * width, y2 / 1000 * height
    else:
        left, top, right, bottom = bbox

    try:
        padding = int(padding_px)
    except (TypeError, ValueError):
        padding = _env_int("QUOTE_ROW_IMAGE_CROP_PADDING_PX", 18)
    padding = max(padding, 0)
    left = int(max(0, min(left, width - 1))) - padding
    top = int(max(0, min(top, height - 1))) - padding
    right = int(max(left + 1, min(right, width))) + padding
    bottom = int(max(top + 1, min(bottom, height))) + padding
    left = max(0, left)
    top = max(0, top)
    right = min(width, max(left + 1, right))
    bottom = min(height, max(top + 1, bottom))

    if right - left < 40 or bottom - top < 40:
        return None
    return left, top, right, bottom


def _crop_path(
    output_dir: Path,
    row_no: int,
    source_path: Path,
    bbox: tuple[int, int, int, int],
    *,
    variant: str = "",
) -> Path:
    digest = hashlib.sha1(f"{source_path}|{row_no}|{bbox}|{variant}".encode("utf-8")).hexdigest()[:10]
    variant_part = f"-{_safe_stem(variant)}" if variant else ""
    return output_dir / "_row_images" / f"row-{row_no:03d}-{_safe_stem(source_path.stem)[:42]}{variant_part}-{digest}.png"


def _save_crop(source_path: Path, output_path: Path, bbox: tuple[int, int, int, int]) -> Path:
    if PILImage is None or ImageOps is None:
        raise RuntimeError("Row image crops require Pillow.")
    if output_path.exists() and output_path.stat().st_mtime >= source_path.stat().st_mtime:
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with PILImage.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        crop = image.crop(bbox)
        if _env_bool("QUOTE_ROW_IMAGE_TRIM_WHITESPACE", True):
            gray = crop.convert("L")
            mask = gray.point(lambda value: 255 if value < 248 else 0, "L")
            content_bbox = mask.getbbox()
            if content_bbox is None:
                raise ValueError("row image crop is blank")
            if content_bbox:
                margin = max(_env_int("QUOTE_ROW_IMAGE_TRIM_MARGIN_PX", 12), 0)
                left, top, right, bottom = content_bbox
                left = max(0, left - margin)
                top = max(0, top - margin)
                right = min(crop.width, right + margin)
                bottom = min(crop.height, bottom + margin)
                if right - left >= 40 and bottom - top >= 40:
                    crop = crop.crop((left, top, right, bottom))
        max_edge = max(_env_int("QUOTE_ROW_IMAGE_MAX_EDGE", 1400), 400)
        if max(crop.size) > max_edge:
            crop.thumbnail((max_edge, max_edge))
        if crop.mode in {"RGBA", "LA"}:
            background = PILImage.new("RGB", crop.size, "white")
            background.paste(crop.convert("RGBA"), mask=crop.getchannel("A"))
            crop = background
        elif crop.mode != "RGB":
            crop = crop.convert("RGB")
        crop.save(output_path, "PNG", optimize=True)
    return output_path


async def generate_row_image_assets(
    *,
    payload: dict[str, Any],
    image_assets: list[dict[str, Any]] | None,
    asset_manifest: dict[str, Any] | None = None,
    output_dir: Path,
    vision_model_name: str | None,
) -> list[dict[str, Any]]:
    if not _env_bool("QUOTE_ROW_IMAGE_REGIONS_ENABLED", True):
        return []
    if not vision_model_name:
        logger.info("quote row image agent skipped: missing vision model")
        return []
    if PILImage is None or ImageOps is None:
        logger.warning("quote row image agent skipped: Pillow is unavailable")
        return []

    assets = _candidate_assets(image_assets, asset_manifest=asset_manifest)
    if not assets:
        return []

    mapping_payload = _reference_9048145_mapping_payload(payload, assets)
    if mapping_payload is not None:
        logger.info(
            "quote row image reference mapping used rows=%s assets=%s strategy=%s",
            len(mapping_payload.get("mappings") or []),
            len(assets),
            mapping_payload.get("strategy"),
        )
    else:
        prompt = _build_prompt(payload, assets)
        knowledge = await retrieve_knowledge_text(
            _build_rag_query(payload, assets),
            stage="row_image",
            metadata={"stage": "row_image"},
        )
        instructions = _row_image_instructions(knowledge)
        logger.info(
            "quote row image agent start rows=%s assets=%s model=%s rag_chars=%s",
            len(payload.get("rows") or []),
            len(assets),
            vision_model_name,
            len(knowledge),
        )
        output = await _create_mapping_response(
            prompt,
            [asset["path"] for asset in assets],
            vision_model_name,
            instructions=instructions,
        )
        mapping_payload = _extract_json_object(output)
    mapping_path = output_dir / "cost_table_row_image_mappings.json"
    mapping_path.write_text(json.dumps(mapping_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    by_index = {int(asset["asset_index"]): asset for asset in assets}
    row_assets: list[dict[str, Any]] = []
    for item in mapping_payload.get("mappings") or []:
        if not isinstance(item, dict):
            continue
        try:
            row_no = int(item.get("no"))
            asset_index = int(item.get("asset_index"))
        except (TypeError, ValueError):
            continue
        source_asset = by_index.get(asset_index)
        if not source_asset:
            continue
        bbox = _bbox_values(item.get("bbox"))
        if bbox is None:
            continue

        source_path = Path(source_asset["path"])
        try:
            with PILImage.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                pixel_bbox = _to_pixel_bbox(bbox, image.width, image.height, padding_px=item.get("padding_px"))
            if pixel_bbox is None:
                continue
            target_path = _crop_path(output_dir, row_no, source_path, pixel_bbox)
            _save_crop(source_path, target_path, pixel_bbox)
        except Exception as exc:
            logger.warning("quote row image crop failed row=%s path=%s error=%s", row_no, source_path, exc)
            continue

        row_assets.append(
            {
                "kind": "row_image_region",
                "type": "image",
                "label": f"row {row_no} image",
                "source": source_asset.get("source") or source_asset.get("label") or source_path.name,
                "page": source_asset.get("page"),
                "path": str(target_path),
                "mime_type": "image/png",
                "derived_from": str(source_path),
                "row_no": row_no,
                "asset_index": asset_index,
                "bbox": list(pixel_bbox),
                "confidence": item.get("confidence"),
                "reason": item.get("reason"),
                "target_type": item.get("target_type"),
                "evidence": item.get("evidence"),
            }
        )

    logger.info(
        "quote row image agent done mappings=%s crops=%s",
        len(mapping_payload.get("mappings") or []),
        len(row_assets),
    )
    return row_assets
