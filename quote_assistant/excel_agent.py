from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent

from .asset_classifier import format_asset_manifest_for_prompt
from .bom_decomposition import bom_decomposition_to_excel_payload, generate_bom_decomposition_payload
from .drawing_regions import create_excel_drawing_region_assets
from .excel_audit import (
    ExcelAuditResult,
    audit_excel_output,
    env_flag,
    env_int,
    format_repair_feedback,
)
from .excel_rules import apply_sheet_metal_template_rules
from .excel_template import ExcelBuildResult, build_sheet_metal_workbook
from .qc import run_agent_with_retries, run_with_retries, work_api_key, work_base_url, work_endpoint_mode
from .responses_text import create_text_response
from .row_images import generate_row_image_assets


logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class ExcelAgentResult:
    workbook: ExcelBuildResult
    payload_path: Path
    audit: ExcelAuditResult | None = None
    audit_path: Path | None = None
    skipped: bool = False


EXCEL_AGENT_INSTRUCTIONS = """
你是钣金报价系统里的 Excel 输出 Agent。

你的唯一任务：把已经生成并审核过的报价报告、识图上下文和用户需求，转换成可填入 Excel 模板的严格 JSON。

硬性规则：
- 只输出 JSON，不要输出 Markdown、解释、代码块或注释。
- 不要重新报价，不要新增报告里没有依据的尺寸、重量、工序、材料或价格。
- 图纸或报告未明确的字段填 null，不要编造。
- 如果不是钣金件、钣金组件、PLATE STEEL BEND、sheet metal、焊接钣金、折弯件或相关 BOM 成本表，返回 rows: []。
- 如果有 BOM 层级，必须保持原顺序，并填写 level、has_children、qty。
- part_number 只能放 ERP号、料号、物料号、Item no. 或零件号；drawing_ref 放图号、客户图号、PDF 文件名或 PW-NAC 这类图纸编号。
- 如果标题栏同时有 图号/PW-NAC 和 料号/ERP号，不要把图号写到 part_number。
- 组件行的 has_children 用 "Y"，叶子物料用 "N"。
- 外购件、紧固件、密封条等若只有采购单价，填写 unit_price；不要伪造材料和工艺成本。
- 钣金件若能从报告获得材料、重量、激光、折弯、焊接、表面处理等参数，则填入对应字段。
- 单位必须与字段名一致：重量 kg，激光长度 m，面积 m2/dm2，工时 hour，金额元。

返回 JSON schema：
{
  "template_type": "sheet_metal_cost",
  "confidence": "high|medium|low",
  "source_summary": "一句话说明数据来源",
  "rows": [
    {
      "no": 1,
      "level": 0,
      "part_number": "string|null",
      "has_children": "Y|N",
      "product_type": "组件（装配）|组件（焊接）|钣金件|机加工件|外购件|紧固件|密封条|O型圈|其他",
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
      "note": "string|null"
    }
  ],
  "assumptions": ["string"],
  "uncertain_items": ["string"]
}
""".strip()

EXCEL_AGENT_INSTRUCTIONS = (
    EXCEL_AGENT_INSTRUCTIONS
    + """

模板拆解要求：
- 不要只输出总成摘要。只要报告或识图上下文中出现 BOM、子件、标准件、密封件、外购件或多个图纸，必须按原层级拆成多行。
- 每个图纸/零件至少尝试形成一行；总成行 level 较低，子件 level 较高。
- 对钣金件，尽量把可见事实放入对应字段：材料、重量、孔加工、切割、折弯/成形、焊接、表面处理。
- 工艺数量可以来自图纸明确事实，例如 6×Ø14、8×Ø13、U形、热浸镀锌；不确定的单价、工时、展开长度、切割长度填 null。
- 外购件、紧固件、密封条必须作为独立行；没有采购单价时 unit_price 填 null。
- 备注 note 要写明字段来源和待确认项，方便模板规则引擎继续映射。
"""
).strip()


def build_excel_agent_prompt(user_prompt: str, vision_context: str, final_report: str) -> str:
    return f"""
用户原始需求：
{user_prompt}

识图上下文：
{vision_context or "无"}

最终报价报告：
{final_report}

请把上面的内容转换为 Excel 模板 JSON。只返回 JSON。
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
            raise ValueError("Excel Agent did not return a JSON object.")
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("Excel Agent JSON must be an object.")
    rows = payload.get("rows")
    if rows is None:
        payload["rows"] = []
    elif not isinstance(rows, list):
        raise ValueError("Excel Agent JSON rows must be an array.")
    return payload


def build_excel_agent_prompt(
    user_prompt: str,
    vision_context: str,
    final_report: str,
    bom_decomposition: dict[str, Any] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    excel_audit_feedback: str | None = None,
) -> str:
    bom_block = ""
    if bom_decomposition:
        bom_block = f"""

BOM / total drawing decomposition JSON:
{json.dumps(bom_decomposition, ensure_ascii=False, indent=2)}

Use the BOM decomposition JSON as the preferred row source. Do not collapse its rows.
If a standalone/detail drawing duplicates a child BOM part_number, keep only the child row
inside the assembly hierarchy and merge the standalone drawing facts into that child row.
Do not create an extra top-level quote row for that same part unless the detail drawing is not
referenced by any assembly BOM in the uploaded set.
Only normalize it into the Excel template schema and fill fields that are directly
supported by the decomposition, drawing recognition context, or reviewed report.
"""
    asset_block = ""
    asset_text = format_asset_manifest_for_prompt(asset_manifest, max_chars=4200)
    if asset_text:
        asset_block = f"""

Asset classification manifest:
{asset_text}

Use this manifest to distinguish evidence types:
- technical_drawing and bom_table are primary evidence for dimensions, materials, quantities, item numbers, and drawing facts.
- render / exploded CAD views are useful for row matching and visual evidence.
- photo is appearance reference only and must not be used as dimensional evidence.
- title_block and notes are metadata, not row-image evidence.
"""
    feedback_block = ""
    if excel_audit_feedback:
        feedback_block = f"""

Previous Excel audit feedback:
{excel_audit_feedback}

Regenerate the Excel JSON to address the feedback. Do not invent missing facts.
If a field cannot be verified from the provided evidence, keep it null and mark it
as to-be-confirmed in note/uncertain_items.
"""
    return f"""
User request:
{user_prompt}

Page-level drawing recognition context:
{vision_context or "None"}

Reviewed quotation report:
{final_report}
{bom_block}
{asset_block}
{feedback_block}

Convert the content above to strict Excel template JSON. Return JSON only.
""".strip()


async def generate_excel_payload(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    work_model: Any,
    work_model_name: str | None,
    bom_decomposition: dict[str, Any] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    excel_audit_feedback: str | None = None,
) -> dict[str, Any]:
    prompt = build_excel_agent_prompt(
        user_prompt,
        vision_context,
        final_report,
        bom_decomposition=bom_decomposition,
        asset_manifest=asset_manifest,
        excel_audit_feedback=excel_audit_feedback,
    )
    endpoint_mode = work_endpoint_mode()
    model_name = work_model_name or (work_model if isinstance(work_model, str) else None)

    logger.info(
        "quote excel agent start endpoint_mode=%s model=%s report_chars=%s",
        endpoint_mode,
        model_name,
        len(final_report),
    )

    if endpoint_mode == "responses":
        if not model_name:
            raise RuntimeError("Missing work model name for Excel Agent Responses call.")
        output = await run_with_retries(
            lambda: create_text_response(
                prompt=prompt,
                model_name=str(model_name),
                instructions=EXCEL_AGENT_INSTRUCTIONS,
                base_url=work_base_url(),
                api_key=work_api_key(),
                stream_env_name="QUOTE_EXCEL_STREAM",
            )
        )
    else:
        agent = Agent(
            name="quote_excel_output_agent",
            model=work_model,
            instructions=EXCEL_AGENT_INSTRUCTIONS,
        )
        result = await run_agent_with_retries(agent, prompt)
        output = str(result.final_output)

    payload = extract_json_object(output)
    logger.info("quote excel agent done rows=%s", len(payload.get("rows") or []))
    return payload


async def generate_sheet_metal_excel(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    work_model: Any,
    work_model_name: str | None,
    output_path: Path,
    payload_path: Path,
    image_assets: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    vision_files: list[Path] | None = None,
    vision_model_name: str | None = None,
    review_model: Any | None = None,
    review_model_name: str | None = None,
) -> ExcelAgentResult | None:
    drawing_region_assets = create_excel_drawing_region_assets(image_assets, output_path.parent)
    bom_vision_files = list(vision_files or [])
    if os.getenv("QUOTE_BOM_REGION_IMAGES_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }:
        try:
            max_region_files = max(int(os.getenv("QUOTE_BOM_REGION_IMAGE_MAX_FILES", "12") or 12), 0)
        except ValueError:
            max_region_files = 12
        bom_vision_files.extend(
            Path(str(asset["path"]))
            for asset in drawing_region_assets[:max_region_files]
            if asset.get("path")
        )

    bom_payload: dict[str, Any] | None = None
    bom_excel_payload: dict[str, Any] | None = None
    bom_enabled = os.getenv("QUOTE_BOM_DECOMPOSITION_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    if bom_enabled:
        try:
            bom_payload = await generate_bom_decomposition_payload(
                user_prompt=user_prompt,
                vision_context=vision_context,
                final_report=final_report,
                work_model=work_model,
                work_model_name=work_model_name,
                vision_files=bom_vision_files,
                vision_model_name=vision_model_name,
            )
            bom_payload_path = payload_path.with_name("cost_table_bom_payload.json")
            bom_payload_path.parent.mkdir(parents=True, exist_ok=True)
            bom_payload_path.write_text(json.dumps(bom_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            bom_excel_payload = bom_decomposition_to_excel_payload(bom_payload)
        except Exception:
            logger.exception("quote bom decomposition agent failed; falling back to Excel agent only")

    audit_enabled = env_flag("QUOTE_EXCEL_AUDIT_ENABLED", True)
    audit_path = payload_path.with_name("cost_table_audit.json")
    max_audit_retries = max(env_int("QUOTE_EXCEL_AUDIT_MAX_RETRIES", 1), 0) if audit_enabled else 0
    candidate_output_path = output_path.with_name(f"{output_path.stem}.candidate{output_path.suffix}")
    audit_feedback: str | None = None
    final_result: ExcelAgentResult | None = None

    for attempt_index in range(max_audit_retries + 1):
        attempt_number = attempt_index + 1
        payload = await generate_excel_payload(
            user_prompt=user_prompt,
            vision_context=vision_context,
            final_report=final_report,
            work_model=work_model,
            work_model_name=work_model_name,
            bom_decomposition=bom_payload,
            asset_manifest=asset_manifest,
            excel_audit_feedback=audit_feedback,
        )

        rows = payload.get("rows") or []
        bom_rows = (bom_excel_payload or {}).get("rows") or []
        if bom_rows and len(rows) < len(bom_rows):
            logger.warning(
                "quote excel agent collapsed bom rows; using bom decomposition rows excel_rows=%s bom_rows=%s",
                len(rows),
                len(bom_rows),
            )
            payload = bom_excel_payload or payload
            rows = payload.get("rows") or []

        if not rows:
            logger.info("quote excel agent skipped: no sheet-metal rows")
            return None

        payload_path.parent.mkdir(parents=True, exist_ok=True)
        raw_payload_path = payload_path.with_name(
            "cost_table_agent_payload.json"
            if attempt_number == 1
            else f"cost_table_agent_payload_retry_{attempt_number}.json"
        )
        raw_payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = apply_sheet_metal_template_rules(
            payload,
            user_prompt=user_prompt,
            vision_context=vision_context,
            final_report=final_report,
        )
        logger.info(
            "quote excel template rules applied attempt=%s input_rows=%s output_rows=%s uncertain_items=%s",
            attempt_number,
            len(rows),
            len(payload.get("rows") or []),
            len(payload.get("uncertain_items") or []),
        )
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            row_image_assets = await generate_row_image_assets(
                payload=payload,
                image_assets=image_assets,
                asset_manifest=asset_manifest,
                output_dir=output_path.parent,
                vision_model_name=vision_model_name,
            )
        except Exception:
            logger.exception("quote row image agent failed; falling back to page-level images")
            row_image_assets = []

        workbook_image_assets = [*row_image_assets, *drawing_region_assets, *(image_assets or [])]
        workbook_output_path = candidate_output_path if audit_enabled else output_path
        workbook = build_sheet_metal_workbook(
            payload,
            workbook_output_path,
            image_assets=workbook_image_assets,
        )

        audit: ExcelAuditResult | None = None
        if audit_enabled:
            audit = await audit_excel_output(
                user_prompt=user_prompt,
                vision_context=vision_context,
                final_report=final_report,
                payload=payload,
                workbook=workbook,
                review_model=review_model,
                review_model_name=review_model_name,
                attempts=attempt_number,
            )
            audit_path.write_text(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                "quote excel audit result attempt=%s verdict=%s quality=%s auto_retry=%s",
                attempt_number,
                audit.verdict,
                audit.quality_level,
                audit.can_auto_retry,
            )
            if audit.verdict == "fail" and audit.can_auto_retry and attempt_index < max_audit_retries:
                audit_feedback = format_repair_feedback(audit)
                continue

            if audit.downloadable:
                if output_path.exists():
                    output_path.unlink()
                candidate_output_path.replace(output_path)
                workbook = ExcelBuildResult(
                    path=output_path,
                    row_count=workbook.row_count,
                    warnings=workbook.warnings,
                    image_count=workbook.image_count,
                )
            final_result = ExcelAgentResult(
                workbook=workbook,
                payload_path=payload_path,
                audit=audit,
                audit_path=audit_path,
                skipped=not audit.downloadable,
            )
            break

        final_result = ExcelAgentResult(workbook=workbook, payload_path=payload_path)
        break

    return final_result
