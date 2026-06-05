from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .excel_template import ExcelBuildResult
from .office_events import log_office_event


logger = logging.getLogger("uvicorn.error")

VERDICT_ORDER = {"pass": 0, "needs_confirmation": 1, "fail": 2}
QUALITY_ORDER = {"A": 0, "B": 1, "C": 2}

IDENTITY_FIELDS = ("part_number", "drawing_ref")
PRICE_FIELDS = (
    "unit_price",
    "bom_price",
    "material_unit_price",
    "laser_cut_unit_price",
    "laser_hole_unit_price",
    "blanking_other_unit_price",
    "chamfer_unit_price",
    "tapping_unit_price",
    "polishing_unit_price",
    "bend_unit_price",
    "edge_trim_unit_price",
    "milling_unit_price",
    "brushing_unit_price",
    "punching_unit_price",
    "rivet_unit_price",
    "welding_unit_price",
    "other_process_unit_price",
    "plating_unit_price",
    "spraying_unit_price",
    "hot_dip_zinc_unit_price",
    "zinc_repair_unit_price",
    "surface_unit_price",
    "packing_cost",
    "shipping_cost",
)
SHEET_METAL_REQUIRED_FIELDS = ("material_drawing", "net_weight_kg")
SHEET_METAL_PROCESS_FIELDS = (
    "laser_cut_length_m",
    "laser_hole_length_m",
    "blanking_other_process_name",
    "blanking_other_qty",
    "bend_count",
    "edge_trim_hours",
    "milling_hours",
    "brushing_area_m2",
    "punching_qty",
    "rivet_qty",
    "welding_hours",
    "other_process_name",
    "other_process_qty",
    "plating_weight_kg",
    "spraying_area_dm2",
    "hot_dip_zinc_qty",
    "zinc_repair_hours",
    "surface_process_name",
    "surface_qty",
)
UNCERTAIN_MARKERS = (
    "待确认",
    "未识别",
    "无法确认",
    "缺少",
    "缺失",
    "未提供",
    "不确定",
    "unknown",
    "missing",
    "not provided",
    "to be confirmed",
)

EXCEL_AUDIT_INSTRUCTIONS = """
你是独立报价系统里的 Excel 成本表审核 Agent。

你的任务是审核 Excel payload 是否可以作为真实成本拆解表输出。你不是报价 Agent，
不要补充新价格、不要重新编造尺寸、不要把缺失项当作确定值。

审核原则：
- Excel 里的确定值必须能从图纸识别摘要、BOM 拆解、已审核报告、用户输入或模板规则中找到依据。
- 如果缺少材料单价、工艺单价、工时、采购价或客户价格，不要判定为正式 pass；应判定为 needs_confirmation。
- 如果 BOM 行、零件号、图号、数量、材料、重量、工艺字段明显缺失或不一致，应判定 fail。
- 如果规则校验已经给出 fail，不允许改判 pass。
- 图示匹配不足、把整页图/备注区当零件图、缺少零件本体图示时，至少判定 needs_confirmation；严重时 fail。
- pass 代表 Excel 可作为正式成本表输出；needs_confirmation 代表真实但不是正式报价；fail 代表不应开放正式下载。

只返回严格 JSON，不要返回 Markdown、解释或代码块：
{
  "verdict": "pass|needs_confirmation|fail",
  "quality_level": "A|B|C",
  "confidence": "high|medium|low",
  "issues": ["问题1"],
  "missing_fields": ["字段或参数"],
  "warnings": ["提示"],
  "repair_prompt": "如果可自动修复，写给 Excel Agent 的具体修复要求；否则留空",
  "can_auto_retry": true
}
""".strip()


@dataclass(frozen=True)
class ExcelAuditResult:
    verdict: str
    quality_level: str
    confidence: str
    issues: list[str]
    missing_fields: list[str]
    warnings: list[str]
    repair_prompt: str
    can_auto_retry: bool
    rule_issues: list[str]
    rule_warnings: list[str]
    agent_issues: list[str]
    raw: str = ""
    attempts: int = 1

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def downloadable(self) -> bool:
        return self.verdict in {"pass", "needs_confirmation"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def _as_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _as_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _normalize_verdict(value: Any, default: str = "fail") -> str:
    verdict = str(value or "").strip().lower()
    if verdict in VERDICT_ORDER:
        return verdict
    if verdict == "needs-confirmation":
        return "needs_confirmation"
    return default


def _normalize_quality(value: Any, verdict: str) -> str:
    quality = str(value or "").strip().upper()
    if quality in QUALITY_ORDER:
        return quality
    if verdict == "pass":
        return "A"
    if verdict == "needs_confirmation":
        return "B"
    return "C"


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    return confidence if confidence in {"high", "medium", "low"} else "medium"


def _to_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _worst_verdict(*values: str) -> str:
    return max((_normalize_verdict(value) for value in values), key=lambda item: VERDICT_ORDER[item])


def _worst_quality(*values: str) -> str:
    return max((_normalize_quality(value, "fail") for value in values), key=lambda item: QUALITY_ORDER[item])


def _row_label(row: dict[str, Any], index: int) -> str:
    return str(
        row.get("part_number")
        or row.get("drawing_ref")
        or row.get("description")
        or f"第 {index + 1} 行"
    )


def _is_sheet_metal_row(row: dict[str, Any]) -> bool:
    text = _as_text(
        row.get("product_type"),
        row.get("description"),
        row.get("remark"),
        row.get("drawing_ref"),
        row.get("note"),
    )
    return _contains_any(
        text,
        (
            "钣金",
            "sheet metal",
            "plate",
            "bend",
            "bracket",
            "support",
            "housing",
            "panel",
            "钢板",
            "支架",
        ),
    )


def _has_uncertainty_note(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    text = _as_text(row.get("note"), payload.get("uncertain_items"), payload.get("assumptions"))
    return _contains_any(text, UNCERTAIN_MARKERS)


def _has_price_inputs(rows: list[dict[str, Any]]) -> bool:
    return any(not _is_blank(row.get(field)) for row in rows for field in PRICE_FIELDS)


def _has_process_facts(row: dict[str, Any]) -> bool:
    return any(not _is_blank(row.get(field)) for field in SHEET_METAL_PROCESS_FIELDS)


def _needs_price_confirmation(rows: list[dict[str, Any]]) -> bool:
    leaf_rows = [
        row
        for row in rows
        if str(row.get("has_children") or "").strip().upper() != "Y"
    ]
    cost_rows = [row for row in leaf_rows if _is_sheet_metal_row(row)]
    return bool(cost_rows and not _has_price_inputs(cost_rows))


def run_excel_rule_audit(
    payload: dict[str, Any],
    workbook: ExcelBuildResult | None = None,
) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    row_payloads = [row for row in rows or [] if isinstance(row, dict)]
    fail_issues: list[str] = []
    needs_issues: list[str] = []
    warnings: list[str] = []
    missing_fields: list[str] = []

    if not row_payloads:
        fail_issues.append("Excel payload 没有可写入的成本对象行。")

    for index, row in enumerate(row_payloads):
        label = _row_label(row, index)
        if all(_is_blank(row.get(field)) for field in IDENTITY_FIELDS):
            fail_issues.append(f"{label} 缺少 part_number 或 drawing_ref，无法追溯来源。")
            missing_fields.append(f"{label}: part_number/drawing_ref")

        qty = _as_float(row.get("qty"))
        if qty is None or qty <= 0:
            fail_issues.append(f"{label} 数量 qty 缺失或不是正数。")
            missing_fields.append(f"{label}: qty")

        if _is_blank(row.get("product_type")):
            fail_issues.append(f"{label} 缺少 product_type，无法判断成本规则。")
            missing_fields.append(f"{label}: product_type")

        if str(row.get("has_children") or "").strip().upper() == "Y":
            parent_level = _as_float(row.get("level")) or 0
            has_child_after = any(
                (_as_float(candidate.get("level")) or 0) > parent_level
                for candidate in row_payloads[index + 1 :]
            )
            if not has_child_after:
                fail_issues.append(f"{label} 标记为组件 has_children=Y，但后续没有更高层级子件。")

        if _is_sheet_metal_row(row) and str(row.get("has_children") or "").strip().upper() != "Y":
            for field in SHEET_METAL_REQUIRED_FIELDS:
                if _is_blank(row.get(field)):
                    missing_fields.append(f"{label}: {field}")
                    if _has_uncertainty_note(row, payload):
                        needs_issues.append(f"{label} 缺少 {field}，但已标记待确认。")
                    else:
                        fail_issues.append(f"{label} 缺少 {field}，且未在备注/不确定项中标记待确认。")

            if not _has_process_facts(row):
                missing_fields.append(f"{label}: process fields")
                if _has_uncertainty_note(row, payload):
                    needs_issues.append(f"{label} 没有结构化工艺字段，需人工确认。")
                else:
                    fail_issues.append(f"{label} 没有结构化工艺字段，也没有标记待确认。")

    if _needs_price_confirmation(row_payloads):
        needs_issues.append("材料单价、工艺单价或外协价格未提供，Excel 不能作为正式总价报价。")
        missing_fields.append("price/process unit prices")

    if workbook:
        warnings.extend(workbook.warnings)
        if workbook.row_count != len(row_payloads):
            fail_issues.append(
                f"Excel 写入行数 {workbook.row_count} 与 payload 行数 {len(row_payloads)} 不一致。"
            )
        if workbook.image_count <= 0 and row_payloads:
            needs_issues.append("Excel 未插入任何行级图示，零件图示对应关系需要复核。")
        elif workbook.image_count < len(row_payloads):
            needs_issues.append(
                f"Excel 只插入 {workbook.image_count} 张行级图示，少于 {len(row_payloads)} 行成本对象。"
            )

    if fail_issues:
        verdict = "fail"
        quality_level = "C"
    elif needs_issues or missing_fields:
        verdict = "needs_confirmation"
        quality_level = "B"
    else:
        verdict = "pass"
        quality_level = "A"

    can_auto_retry = verdict == "fail" and any(
        marker in issue
        for issue in fail_issues
        for marker in ("缺少", "没有结构化", "未在备注")
    )
    repair_prompt = ""
    if can_auto_retry:
        repair_prompt = (
            "请重新生成 Excel payload：补齐可从图纸识别摘要、BOM、报告中确认的字段；"
            "无法确认的字段必须保持 null，并在 note/uncertain_items 中明确写待确认依据。"
        )

    return {
        "verdict": verdict,
        "quality_level": quality_level,
        "confidence": "high" if verdict == "fail" else "medium",
        "issues": [*fail_issues, *needs_issues],
        "missing_fields": sorted(set(missing_fields)),
        "warnings": warnings,
        "repair_prompt": repair_prompt,
        "can_auto_retry": can_auto_retry,
    }


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
            raise ValueError("Excel audit agent did not return a JSON object.")
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("Excel audit agent JSON must be an object.")
    return payload


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"


def build_excel_audit_prompt(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    payload: dict[str, Any],
    rule_audit: dict[str, Any],
) -> str:
    return f"""
用户原始需求：
{_clip(user_prompt, 2000)}

图纸识别上下文：
{_clip(vision_context or "无", 12000)}

已审核报价报告：
{_clip(final_report or "无", 8000)}

Excel payload JSON：
{_clip(json.dumps(payload, ensure_ascii=False, indent=2), 18000)}

确定性规则校验结果：
{json.dumps(rule_audit, ensure_ascii=False, indent=2)}

请审核这个 Excel 是否可以作为真实成本拆解表输出。
""".strip()


async def run_excel_audit_agent(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    payload: dict[str, Any],
    rule_audit: dict[str, Any],
    review_model: Any | None,
    review_model_name: str | None,
) -> tuple[dict[str, Any], str]:
    prompt = build_excel_audit_prompt(
        user_prompt=user_prompt,
        vision_context=vision_context,
        final_report=final_report,
        payload=payload,
        rule_audit=rule_audit,
    )
    from .qc import (
        review_api_key,
        review_base_url,
        review_endpoint_mode,
        run_agent_with_retries,
        run_with_retries,
    )
    from .responses_text import create_text_response

    endpoint_mode = review_endpoint_mode()
    model_name = review_model_name or os.getenv("QUOTE_REVIEW_MODEL")

    logger.info(
        "quote excel audit agent start endpoint_mode=%s model=%s rows=%s",
        endpoint_mode,
        model_name,
        len(payload.get("rows") or []),
    )
    log_office_event(
        "quote_excel_audit_agent",
        "excel_audit_started",
        status="running",
        message="quote_excel_audit_agent 开始审核 Excel payload。",
        metadata={"endpoint_mode": endpoint_mode, "model": model_name, "rows": len(payload.get("rows") or [])},
    )
    try:
        if endpoint_mode == "responses":
            if not model_name:
                raise RuntimeError("Missing review model name for Excel audit Responses call.")
            output = await run_with_retries(
                lambda: create_text_response(
                    prompt=prompt,
                    model_name=str(model_name),
                    instructions=EXCEL_AUDIT_INSTRUCTIONS,
                    base_url=review_base_url(),
                    api_key=review_api_key(),
                    stream_env_name="QUOTE_EXCEL_AUDIT_STREAM",
                )
            )
        else:
            if review_model is None:
                raise RuntimeError("Missing review model object for Excel audit chat call.")
            from agents import Agent

            agent = Agent(
                name="quote_excel_audit_agent",
                model=review_model,
                instructions=EXCEL_AUDIT_INSTRUCTIONS,
            )
            result = await run_agent_with_retries(agent, prompt)
            output = str(result.final_output)

        logger.info("quote excel audit agent done chars=%s", len(output))
        parsed = extract_json_object(output)
        log_office_event(
            "quote_excel_audit_agent",
            "excel_audit_completed",
            status="done",
            message="quote_excel_audit_agent 已完成 Excel payload 审核。",
            metadata={
                "endpoint": endpoint_mode,
                "chars": len(output),
                "verdict": parsed.get("verdict"),
                "quality_level": parsed.get("quality_level"),
            },
        )
        return parsed, output
    except Exception as exc:
        log_office_event(
            "quote_excel_audit_agent",
            "excel_audit_failed",
            status="failed",
            message="quote_excel_audit_agent Excel payload 审核失败。",
            metadata={"endpoint_mode": endpoint_mode, "model": model_name, "rows": len(payload.get("rows") or [])},
            error=str(exc),
        )
        raise


def merge_excel_audit_results(
    rule_audit: dict[str, Any],
    agent_audit: dict[str, Any] | None,
    raw: str = "",
    attempts: int = 1,
) -> ExcelAuditResult:
    if agent_audit is None:
        agent_audit = {
            "verdict": "fail",
            "quality_level": "C",
            "confidence": "low",
            "issues": ["Excel 审核 Agent 未返回结果。"],
            "missing_fields": [],
            "warnings": [],
            "repair_prompt": "",
            "can_auto_retry": False,
        }

    rule_verdict = _normalize_verdict(rule_audit.get("verdict"), default="fail")
    agent_verdict = _normalize_verdict(agent_audit.get("verdict"), default="fail")
    verdict = _worst_verdict(rule_verdict, agent_verdict)

    rule_quality = _normalize_quality(rule_audit.get("quality_level"), rule_verdict)
    agent_quality = _normalize_quality(agent_audit.get("quality_level"), agent_verdict)
    quality_level = _worst_quality(rule_quality, agent_quality)

    rule_issues = _to_text_list(rule_audit.get("issues"))
    agent_issues = _to_text_list(agent_audit.get("issues"))
    issues = [*rule_issues]
    for issue in agent_issues:
        if issue not in issues:
            issues.append(issue)

    missing_fields = sorted(
        set(_to_text_list(rule_audit.get("missing_fields")) + _to_text_list(agent_audit.get("missing_fields")))
    )
    rule_warnings = _to_text_list(rule_audit.get("warnings"))
    warnings = [*rule_warnings]
    for warning in _to_text_list(agent_audit.get("warnings")):
        if warning not in warnings:
            warnings.append(warning)

    can_auto_retry = bool(rule_audit.get("can_auto_retry") or agent_audit.get("can_auto_retry"))
    if verdict != "fail":
        can_auto_retry = False

    repair_prompt = str(agent_audit.get("repair_prompt") or rule_audit.get("repair_prompt") or "").strip()
    confidence = _normalize_confidence(agent_audit.get("confidence") or rule_audit.get("confidence"))

    return ExcelAuditResult(
        verdict=verdict,
        quality_level=quality_level,
        confidence=confidence,
        issues=issues,
        missing_fields=missing_fields,
        warnings=warnings,
        repair_prompt=repair_prompt,
        can_auto_retry=can_auto_retry,
        rule_issues=rule_issues,
        rule_warnings=rule_warnings,
        agent_issues=agent_issues,
        raw=raw,
        attempts=attempts,
    )


async def audit_excel_output(
    *,
    user_prompt: str,
    vision_context: str,
    final_report: str,
    payload: dict[str, Any],
    workbook: ExcelBuildResult | None,
    review_model: Any | None,
    review_model_name: str | None,
    attempts: int = 1,
) -> ExcelAuditResult:
    rule_audit = run_excel_rule_audit(payload, workbook)
    if not env_flag("QUOTE_EXCEL_AUDIT_AGENT_ENABLED", True):
        return merge_excel_audit_results(rule_audit, rule_audit, attempts=attempts)

    try:
        agent_audit, raw = await run_excel_audit_agent(
            user_prompt=user_prompt,
            vision_context=vision_context,
            final_report=final_report,
            payload=payload,
            rule_audit=rule_audit,
            review_model=review_model,
            review_model_name=review_model_name,
        )
    except Exception as exc:
        logger.exception("quote excel audit agent failed")
        agent_audit = {
            "verdict": "fail",
            "quality_level": "C",
            "confidence": "low",
            "issues": [f"Excel 审核 Agent 调用失败：{exc}"],
            "missing_fields": [],
            "warnings": [],
            "repair_prompt": "",
            "can_auto_retry": False,
        }
        raw = str(exc)

    return merge_excel_audit_results(rule_audit, agent_audit, raw=raw, attempts=attempts)


def format_repair_feedback(audit: ExcelAuditResult) -> str:
    issues = "\n".join(f"- {issue}" for issue in audit.issues) or "- 未给出具体问题"
    missing = "\n".join(f"- {item}" for item in audit.missing_fields) or "- 无"
    repair = audit.repair_prompt or "请根据问题重新生成 Excel payload。"
    return f"""
上一版 Excel 审核未通过。

审核问题：
{issues}

缺失字段：
{missing}

修复要求：
{repair}
""".strip()
