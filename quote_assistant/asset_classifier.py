from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from .io import build_agent_input
from .responses_text import create_streaming_response, env_flag, env_float, response_text
from .responses_vision import api_key as vision_api_key
from .responses_vision import responses_endpoint as vision_responses_endpoint


logger = logging.getLogger("uvicorn.error")


ASSET_CLASSIFIER_INSTRUCTIONS = """
You are a drawing asset classifier for a quotation workflow.

Your job is to classify each attached image/page before later agents extract dimensions,
BOM rows, and Excel row illustrations.

Return JSON only. No Markdown, no code fences, no explanatory prose.

Classify source_type as one of:
- technical_drawing: engineering drawing, CAD drawing, detail drawing, section view, flat pattern.
- photo: real camera photo of a physical object or sample.
- render: CAD shaded isometric, exploded view, 3D model rendering, non-photo product view.
- bom_table: page or crop dominated by BOM, parts list, cost table, or material table.
- title_block: page or crop dominated by title block, drawing frame, revision block.
- notes: page or crop dominated by technical requirements or free-text notes.
- mixed: multiple important source types are visible on the same page.
- unknown: insufficient visual evidence.

Important rules:
- A shaded CAD model, exploded CAD view, or isometric model is render, not photo.
- Real photos can support appearance, assembly state, finish, and texture, but not drawing dimensions.
- Technical drawings and BOM tables are the preferred evidence for dimensions, quantities,
  materials, item numbers, callouts, and row-image crops.
- Title blocks, notes, and revision tables are metadata only. Do not use them as row images.
- If the page is mixed, provide regions with normalized 0-1000 bboxes for the main drawing,
  photo, render, BOM table, notes, and title block areas when visible.
- Regions should be coarse but useful. Do not over-segment small text lines.

Return this JSON schema:
{
  "summary": {
    "has_technical_drawing": true,
    "has_photo": false,
    "has_render": true,
    "has_bom_table": true,
    "input_profile": "pure_drawing|drawing_with_photo|drawing_with_render|photo_only|mixed|unknown"
  },
  "assets": [
    {
      "asset_index": 1,
      "label": "string",
      "source": "string",
      "page": 1,
      "source_type": "technical_drawing|photo|render|bom_table|title_block|notes|mixed|unknown",
      "contains_technical_drawing": true,
      "contains_photo": false,
      "contains_render": true,
      "contains_bom_table": true,
      "usage_hint": "short guidance for downstream agents",
      "regions": [
        {
          "type": "technical_drawing|photo|render|bom_table|title_block|notes|unknown",
          "bbox": [0, 0, 1000, 1000],
          "confidence": "high|medium|low",
          "usage": "dimensions|bom|row_image|appearance|metadata|ignore"
        }
      ]
    }
  ]
}
""".strip()


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


def _compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _candidate_assets(image_assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    max_assets = max(_env_int("QUOTE_ASSET_CLASSIFIER_MAX_IMAGES", 18), 1)
    candidates: list[dict[str, Any]] = []
    for asset in image_assets or []:
        if len(candidates) >= max_assets:
            break
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        raw_path = str(asset.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        candidates.append(
            {
                "asset_index": len(candidates) + 1,
                "label": str(asset.get("label") or path.name),
                "source": str(asset.get("source") or ""),
                "page": asset.get("page"),
                "kind": asset.get("kind"),
                "path": path,
            }
        )
    return candidates


def _build_prompt(candidates: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in candidates:
        lines.append(
            " | ".join(
                [
                    f"asset_index={item['asset_index']}",
                    f"label={item['label']}",
                    f"source={item.get('source') or ''}",
                    f"page={item.get('page') or ''}",
                    f"kind={item.get('kind') or ''}",
                ]
            )
        )
    return f"""
Classify the attached source images in the same order as this list:

{chr(10).join(lines)}

Return the asset classification JSON for all listed asset_index values.
""".strip()


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
            raise ValueError("Asset classifier did not return a JSON object.")
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("Asset classifier JSON must be an object.")
    assets = payload.get("assets")
    if assets is None:
        payload["assets"] = []
    elif not isinstance(assets, list):
        raise ValueError("Asset classifier JSON assets must be an array.")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        payload["summary"] = {}
    return payload


def _normalize_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    allowed = {
        "technical_drawing",
        "photo",
        "render",
        "bom_table",
        "title_block",
        "notes",
        "mixed",
        "unknown",
    }
    return text if text in allowed else "unknown"


def _normalize_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if max(abs(item) for item in bbox) <= 1.5:
        bbox = [item * 1000 for item in bbox]
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(1000.0, x1))
    y1 = max(0.0, min(1000.0, y1))
    x2 = max(0.0, min(1000.0, x2))
    y2 = max(0.0, min(1000.0, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]


def _infer_summary(asset_items: list[dict[str, Any]]) -> dict[str, Any]:
    has_technical = any(bool(item.get("contains_technical_drawing")) for item in asset_items)
    has_photo = any(bool(item.get("contains_photo")) for item in asset_items)
    has_render = any(bool(item.get("contains_render")) for item in asset_items)
    has_bom = any(bool(item.get("contains_bom_table")) for item in asset_items)
    if has_technical and has_photo:
        profile = "drawing_with_photo"
    elif has_technical and has_render:
        profile = "drawing_with_render"
    elif has_technical:
        profile = "pure_drawing"
    elif has_photo:
        profile = "photo_only"
    elif has_render or has_bom:
        profile = "mixed"
    else:
        profile = "unknown"
    return {
        "has_technical_drawing": has_technical,
        "has_photo": has_photo,
        "has_render": has_render,
        "has_bom_table": has_bom,
        "input_profile": profile,
    }


def _fallback_manifest(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    assets = []
    for item in candidates:
        assets.append(
            {
                "asset_index": item["asset_index"],
                "label": item["label"],
                "source": item.get("source") or "",
                "page": item.get("page"),
                "path": str(item["path"]),
                "source_type": "unknown",
                "contains_technical_drawing": False,
                "contains_photo": False,
                "contains_render": False,
                "contains_bom_table": False,
                "usage_hint": "Unclassified image. Downstream agents should inspect it cautiously.",
                "regions": [],
            }
        )
    return {"summary": _infer_summary(assets), "assets": assets}


def _normalize_manifest(payload: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_index = {
        int(item.get("asset_index")): item
        for item in payload.get("assets") or []
        if isinstance(item, dict) and str(item.get("asset_index") or "").isdigit()
    }
    assets: list[dict[str, Any]] = []
    for candidate in candidates:
        raw = by_index.get(int(candidate["asset_index"])) or {}
        source_type = _normalize_type(raw.get("source_type"))
        regions = []
        for region in raw.get("regions") or []:
            if not isinstance(region, dict):
                continue
            bbox = _normalize_bbox(region.get("bbox"))
            if bbox is None:
                continue
            regions.append(
                {
                    "type": _normalize_type(region.get("type")),
                    "bbox": bbox,
                    "confidence": str(region.get("confidence") or "medium"),
                    "usage": str(region.get("usage") or ""),
                }
            )
        contains_technical = bool(raw.get("contains_technical_drawing")) or source_type == "technical_drawing"
        contains_photo = bool(raw.get("contains_photo")) or source_type == "photo"
        contains_render = bool(raw.get("contains_render")) or source_type == "render"
        contains_bom = bool(raw.get("contains_bom_table")) or source_type == "bom_table"
        assets.append(
            {
                "asset_index": candidate["asset_index"],
                "label": candidate["label"],
                "source": candidate.get("source") or "",
                "page": candidate.get("page"),
                "path": str(candidate["path"]),
                "source_type": source_type,
                "contains_technical_drawing": contains_technical,
                "contains_photo": contains_photo,
                "contains_render": contains_render,
                "contains_bom_table": contains_bom,
                "usage_hint": _compact(raw.get("usage_hint"), 500)
                or _default_usage_hint(source_type, contains_technical, contains_photo, contains_render, contains_bom),
                "regions": regions,
            }
        )

    summary = dict(payload.get("summary") or {})
    inferred = _infer_summary(assets)
    for key, value in inferred.items():
        summary.setdefault(key, value)
    return {"summary": summary, "assets": assets}


def _default_usage_hint(
    source_type: str,
    contains_technical: bool,
    contains_photo: bool,
    contains_render: bool,
    contains_bom: bool,
) -> str:
    if source_type == "photo" or contains_photo:
        return "Use as appearance and assembly-state reference only; do not use as dimensional evidence."
    if source_type == "bom_table" or contains_bom:
        return "Use for BOM item numbers, quantities, materials, names, and part numbers."
    if source_type == "render" or contains_render:
        return "Use for visual row matching and exploded-view callouts; verify dimensions from drawings."
    if source_type == "technical_drawing" or contains_technical:
        return "Use for dimensions, callouts, geometry, process notes, and row-image evidence."
    return "Use cautiously; classify manually if downstream evidence is ambiguous."


async def _create_classifier_response(prompt: str, files: list[Path], model_name: str) -> str:
    payload = {
        "model": model_name,
        "instructions": ASSET_CLASSIFIER_INSTRUCTIONS,
        "input": build_agent_input(prompt, files),
    }
    headers = {
        "Authorization": f"Bearer {vision_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0 quote-agent-assistant",
    }
    async with httpx.AsyncClient(timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0)) as client:
        if env_flag("QUOTE_ASSET_CLASSIFIER_STREAM", default=True):
            return await create_streaming_response(client, vision_responses_endpoint(), payload, headers)
        response = await client.post(vision_responses_endpoint(), headers=headers, json=payload)

    if response.status_code >= 400:
        raise RuntimeError(f"Responses API error {response.status_code}: {response.text[:1200]}")
    return response_text(response.json())


async def classify_asset_manifest(
    *,
    image_assets: list[dict[str, Any]] | None,
    vision_model_name: str | None,
) -> dict[str, Any]:
    candidates = _candidate_assets(image_assets)
    if not candidates:
        return {"summary": _infer_summary([]), "assets": []}
    if not _env_bool("QUOTE_ASSET_CLASSIFIER_ENABLED", True):
        return _fallback_manifest(candidates)
    if not vision_model_name:
        logger.info("quote asset classifier skipped: missing vision model")
        return _fallback_manifest(candidates)

    try:
        output = await _create_classifier_response(
            _build_prompt(candidates),
            [item["path"] for item in candidates],
            vision_model_name,
        )
        manifest = _normalize_manifest(_extract_json_object(output), candidates)
        logger.info(
            "quote asset classifier done assets=%s profile=%s",
            len(manifest.get("assets") or []),
            (manifest.get("summary") or {}).get("input_profile"),
        )
        return manifest
    except Exception as exc:
        logger.warning("quote asset classifier failed; continuing without classification: %s", exc)
        return _fallback_manifest(candidates)


def format_asset_manifest_for_prompt(manifest: dict[str, Any] | None, *, max_chars: int = 5000) -> str:
    if not manifest:
        return ""
    summary = manifest.get("summary") or {}
    lines = [
        "Asset classification summary:",
        "- input_profile: " + str(summary.get("input_profile") or "unknown"),
        "- has_technical_drawing: " + str(bool(summary.get("has_technical_drawing"))),
        "- has_photo: " + str(bool(summary.get("has_photo"))),
        "- has_render: " + str(bool(summary.get("has_render"))),
        "- has_bom_table: " + str(bool(summary.get("has_bom_table"))),
        "",
        "Classified assets:",
    ]
    for item in manifest.get("assets") or []:
        if not isinstance(item, dict):
            continue
        regions = []
        for region in (item.get("regions") or [])[:8]:
            if not isinstance(region, dict):
                continue
            regions.append(
                f"{region.get('type')} bbox={region.get('bbox')} usage={region.get('usage')}"
            )
        lines.append(
            " | ".join(
                [
                    f"asset_index={item.get('asset_index')}",
                    f"label={_compact(item.get('label'), 80)}",
                    f"source={_compact(item.get('source'), 80)}",
                    f"page={item.get('page') or ''}",
                    f"source_type={item.get('source_type')}",
                    f"usage_hint={_compact(item.get('usage_hint'), 180)}",
                    f"regions={'; '.join(regions) if regions else 'none'}",
                ]
            )
        )
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "..."
    return text
