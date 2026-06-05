from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from agents import Agent

from .io import build_agent_input
from .office_events import log_office_event
from .qc import run_agent_with_retries, run_with_retries, work_api_key, work_base_url, work_endpoint_mode
from .responses_text import create_streaming_response, create_text_response, env_flag, env_float, response_text
from .responses_vision import api_key as vision_api_key
from .responses_vision import responses_endpoint as vision_responses_endpoint


logger = logging.getLogger("uvicorn.error")


BOM_DECOMPOSITION_INSTRUCTIONS = """
You are a BOM and drawing decomposition agent for a sheet-metal quotation system.

Your job is not to calculate a final quotation. Your job is to split assembly drawings,
BOM tables, drawing notes, standard parts, purchased parts, sealing parts, sheet-metal
parts, and process facts into cost objects that can later be written into an Excel
cost template.

Hard rules:
- Return JSON only. Do not return Markdown, explanations, or code fences.
- Do not invent part numbers, materials, dimensions, weights, prices, quantities, or processes.
- If a BOM table, title block, or report lists child parts, output each child part as its own row.
- If an uploaded standalone/detail drawing has the same part_number as a child BOM item, do not
  output a separate top-level quotation row for that detail drawing. Keep the child row in the
  assembly hierarchy and merge the standalone drawing's material, dimensions, process facts,
  drawing_ref, source_refs, and notes into that child row.
- Only output a standalone/detail drawing as a top-level row when it is not referenced by any
  visible assembly BOM/parts list in the uploaded set.
- If the assembly drawing mentions standard parts, fasteners, O-rings, sealing strips, purchased
  profiles, or other bought-out items, output each identifiable item as its own row.
- If a total drawing only references child drawings and does not show all standard parts, do not
  invent missing BOM rows. Put the limitation in uncertain_items.
- Keep hierarchy: assembly rows use a lower level; direct child rows use level + 1.
- part_number must be the ERP/material/item number when it is visible. drawing_ref must be
  the drawing number, drawing file name, or customer drawing reference. If a title block
  shows both 图号/PW-NAC/drawing no. and 料号/ERP号/item no., do not put the drawing number
  into part_number.
- Use product_type values that fit the Excel template:
  "组件（装配）", "组件（焊接）", "钣金件", "机加工件", "外购件", "紧固件", "密封条", "O型圈", "其他".
- For sheet-metal rows, capture visible process facts in template fields when possible:
  material_drawing, net_weight_kg, laser_hole_length_m only if already calculated or explicit,
  blanking_other_process_name, bend_count, other_process_name, surface_process_name, note.
- For process facts that cannot be converted to a numeric field, put them in note and/or
  process_facts. Examples: "6×Ø14", "8×Ø13", "R535", "U-shaped", "hot-dip galvanizing",
  "a5 weld", "100% VT, 5% MT/PT".
- Prices and unit prices should be null unless explicitly provided.
- Quantities must come from BOM/report/drawing facts. If unknown, use 1 and record the uncertainty.

Return JSON schema:
{
  "decomposition_type": "sheet_metal_bom_decomposition",
  "confidence": "high|medium|low",
  "source_summary": "short source summary",
  "rows": [
    {
      "no": 1,
      "level": 0,
      "parent_part_number": "string|null",
      "part_number": "string|null",
      "has_children": "Y|N",
      "product_type": "string|null",
      "drawing_ref": "string|null",
      "remark": "string|null",
      "description": "string|null",
      "qty": 1,
      "unit_price": null,
      "bom_price": null,
      "material_drawing": "string|null",
      "material_substitute": "string|null",
      "raw_weight_kg": null,
      "net_weight_kg": null,
      "material_unit_price": null,
      "laser_cut_unit_price": null,
      "laser_cut_length_m": null,
      "laser_hole_unit_price": null,
      "laser_hole_length_m": null,
      "blanking_other_process_name": "string|null",
      "blanking_other_unit_price": null,
      "blanking_other_qty": null,
      "chamfer_unit_price": null,
      "chamfer_qty": null,
      "tapping_unit_price": null,
      "tapping_qty": null,
      "polishing_unit_price": null,
      "polishing_area_m2": null,
      "bend_unit_price": null,
      "bend_count": null,
      "edge_trim_unit_price": null,
      "edge_trim_hours": null,
      "milling_unit_price": null,
      "milling_hours": null,
      "brushing_unit_price": null,
      "brushing_area_m2": null,
      "punching_unit_price": null,
      "punching_qty": null,
      "rivet_unit_price": null,
      "rivet_qty": null,
      "welding_unit_price": null,
      "welding_hours": null,
      "other_process_name": "string|null",
      "other_process_unit_price": null,
      "other_process_qty": null,
      "plating_unit_price": null,
      "plating_weight_kg": null,
      "spraying_unit_price": null,
      "spraying_area_dm2": null,
      "hot_dip_zinc_unit_price": null,
      "hot_dip_zinc_qty": null,
      "zinc_repair_unit_price": null,
      "zinc_repair_hours": null,
      "surface_process_name": "string|null",
      "surface_unit_price": null,
      "surface_qty": null,
      "packing_cost": null,
      "shipping_cost": null,
      "process_facts": ["string"],
      "source_refs": ["string"],
      "note": "string|null"
    }
  ],
  "uncertain_items": ["string"],
  "assumptions": ["string"]
}
""".strip()


EXCEL_ROW_KEYS = {
    "no",
    "level",
    "part_number",
    "has_children",
    "product_type",
    "drawing_ref",
    "remark",
    "description",
    "qty",
    "unit_price",
    "bom_price",
    "material_drawing",
    "material_substitute",
    "raw_weight_kg",
    "net_weight_kg",
    "material_unit_price",
    "laser_cut_unit_price",
    "laser_cut_length_m",
    "laser_hole_unit_price",
    "laser_hole_length_m",
    "blanking_other_process_name",
    "blanking_other_unit_price",
    "blanking_other_qty",
    "chamfer_unit_price",
    "chamfer_qty",
    "tapping_unit_price",
    "tapping_qty",
    "polishing_unit_price",
    "polishing_area_m2",
    "bend_unit_price",
    "bend_count",
    "edge_trim_unit_price",
    "edge_trim_hours",
    "milling_unit_price",
    "milling_hours",
    "brushing_unit_price",
    "brushing_area_m2",
    "punching_unit_price",
    "punching_qty",
    "rivet_unit_price",
    "rivet_qty",
    "welding_unit_price",
    "welding_hours",
    "other_process_name",
    "other_process_unit_price",
    "other_process_qty",
    "plating_unit_price",
    "plating_weight_kg",
    "spraying_unit_price",
    "spraying_area_dm2",
    "hot_dip_zinc_unit_price",
    "hot_dip_zinc_qty",
    "zinc_repair_unit_price",
    "zinc_repair_hours",
    "surface_process_name",
    "surface_unit_price",
    "surface_qty",
    "packing_cost",
    "shipping_cost",
    "note",
}


def build_bom_decomposition_prompt(user_prompt: str, vision_context: str, final_report: str) -> str:
    return f"""
User request:
{user_prompt}

Page-level drawing recognition context:
{vision_context or "None"}

Reviewed quotation report:
{final_report}

Split the above into detailed BOM/cost-object rows. Preserve hierarchy and do not collapse
assembly drawings into a single row when BOM items, standard parts, purchased parts,
sealing parts, or process facts are visible.
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise ValueError("BOM decomposition agent did not return a JSON object.")
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("BOM decomposition JSON must be an object.")
    rows = payload.get("rows")
    if rows is None:
        payload["rows"] = []
    elif not isinstance(rows, list):
        raise ValueError("BOM decomposition JSON rows must be an array.")
    return payload


async def create_bom_vision_response(prompt: str, files: list[Path], model_name: str) -> str:
    payload = {
        "model": model_name,
        "instructions": BOM_DECOMPOSITION_INSTRUCTIONS,
        "input": build_agent_input(prompt, files),
    }
    headers = {
        "Authorization": f"Bearer {vision_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0 quote-agent-assistant",
    }

    async with httpx.AsyncClient(timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0)) as client:
        if env_flag("QUOTE_BOM_STREAM", default=True):
            return await create_streaming_response(client, vision_responses_endpoint(), payload, headers)
        response = await client.post(vision_responses_endpoint(), headers=headers, json=payload)

    if response.status_code >= 400:
        raise RuntimeError(f"Responses API error {response.status_code}: {response.text[:1200]}")
    return response_text(response.json())


def _append_unique(parts: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in parts:
        parts.append(text)


def _normalize_bom_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    normalized = {key: copy.deepcopy(value) for key, value in row.items() if key in EXCEL_ROW_KEYS}
    normalized.setdefault("no", index + 1)
    normalized.setdefault("qty", 1)
    normalized.setdefault("has_children", "N")

    note_parts: list[str] = []
    _append_unique(note_parts, normalized.get("note"))
    for fact in row.get("process_facts") or []:
        _append_unique(note_parts, fact)
    for ref in row.get("source_refs") or []:
        _append_unique(note_parts, f"source: {ref}")
    if note_parts:
        normalized["note"] = "；".join(note_parts)
    return normalized


def bom_decomposition_to_excel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    row_payloads = [row for row in rows or [] if isinstance(row, dict)]
    return {
        "template_type": "sheet_metal_cost",
        "confidence": payload.get("confidence") or "medium",
        "source_summary": payload.get("source_summary") or "BOM decomposition output.",
        "rows": [_normalize_bom_row(row, index) for index, row in enumerate(row_payloads)],
        "assumptions": list(payload.get("assumptions") or []),
        "uncertain_items": list(payload.get("uncertain_items") or []),
        "bom_decomposition_type": payload.get("decomposition_type") or "sheet_metal_bom_decomposition",
    }


async def generate_bom_decomposition_payload(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    work_model: Any,
    work_model_name: str | None,
    vision_files: list[Path] | None = None,
    vision_model_name: str | None = None,
) -> dict[str, Any]:
    prompt = build_bom_decomposition_prompt(user_prompt, vision_context, final_report)
    endpoint_mode = work_endpoint_mode()
    model_name = work_model_name or (work_model if isinstance(work_model, str) else None)

    logger.info(
        "quote bom decomposition agent start endpoint_mode=%s model=%s report_chars=%s vision_chars=%s",
        endpoint_mode,
        model_name,
        len(final_report),
        len(vision_context),
    )
    log_office_event(
        "quote_bom_decomposition_agent",
        "bom_decomposition_started",
        status="running",
        message="quote_bom_decomposition_agent 开始拆解 BOM/图纸层级。",
        metadata={
            "endpoint_mode": endpoint_mode,
            "model": model_name,
            "report_chars": len(final_report),
            "vision_chars": len(vision_context),
            "vision_files": len(vision_files or []),
        },
    )
    try:
        if vision_files and vision_model_name:
            try:
                output = await run_with_retries(
                    lambda: create_bom_vision_response(prompt, vision_files, vision_model_name)
                )
                payload = extract_json_object(output)
                logger.info(
                    "quote bom decomposition agent done via vision rows=%s files=%s model=%s",
                    len(payload.get("rows") or []),
                    len(vision_files),
                    vision_model_name,
                )
                log_office_event(
                    "quote_bom_decomposition_agent",
                    "bom_decomposition_completed",
                    status="done",
                    message="quote_bom_decomposition_agent 已完成 BOM/图纸层级拆解。",
                    metadata={"endpoint": "vision", "rows": len(payload.get("rows") or []), "files": len(vision_files)},
                )
                return payload
            except Exception as exc:
                log_office_event(
                    "quote_bom_decomposition_agent",
                    "bom_decomposition_fallback",
                    status="running",
                    message="BOM 视觉拆解失败，切换到文本拆解继续。",
                    metadata={"from": "vision", "to": endpoint_mode},
                    error=str(exc),
                )
                logger.warning("quote bom decomposition vision failed; falling back to text: %s", exc)

        if endpoint_mode == "responses":
            if not model_name:
                raise RuntimeError("Missing work model name for BOM decomposition Responses call.")
            output = await run_with_retries(
                lambda: create_text_response(
                    prompt=prompt,
                    model_name=str(model_name),
                    instructions=BOM_DECOMPOSITION_INSTRUCTIONS,
                    base_url=work_base_url(),
                    api_key=work_api_key(),
                    stream_env_name="QUOTE_BOM_STREAM",
                )
            )
        else:
            agent = Agent(
                name="quote_bom_decomposition_agent",
                model=work_model,
                instructions=BOM_DECOMPOSITION_INSTRUCTIONS,
            )
            result = await run_agent_with_retries(agent, prompt)
            output = str(result.final_output)

        payload = extract_json_object(output)
        logger.info("quote bom decomposition agent done rows=%s", len(payload.get("rows") or []))
        log_office_event(
            "quote_bom_decomposition_agent",
            "bom_decomposition_completed",
            status="done",
            message="quote_bom_decomposition_agent 已完成 BOM/图纸层级拆解。",
            metadata={"endpoint": endpoint_mode, "rows": len(payload.get("rows") or [])},
        )
        return payload
    except Exception as exc:
        log_office_event(
            "quote_bom_decomposition_agent",
            "bom_decomposition_failed",
            status="failed",
            message="quote_bom_decomposition_agent BOM/图纸层级拆解失败。",
            metadata={"endpoint_mode": endpoint_mode, "model": model_name},
            error=str(exc),
        )
        raise
