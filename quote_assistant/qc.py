from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Runner

from .agents import SPECIALISTS, build_agent_system, build_review_agent, build_vision_agent, skill_block
from .io import build_agent_input
from .office_events import log_office_event
from .responses_vision import create_vision_summary
from .responses_text import create_text_response


TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
logger = logging.getLogger("uvicorn.error")

REVIEW_SKILL_BLOCKS = "\n\n".join(
    skill_block(skill_name)
    for skill_name in ["drawing-material-analysis", *SPECIALISTS.keys()]
)
REVIEW_INSTRUCTIONS = f"""
你是独立报价系统的质量审核 Agent，只负责判断报价报告是否可以正式输出。

你必须对照用户原始需求、附件图纸/文件、候选报价报告，以及以下全部 skill 规则进行审核：

{REVIEW_SKILL_BLOCKS}

审核重点：
1. 品类识别是否正确，是否派发到了正确专业 Agent。
2. 图纸明确内容、推断内容、模板默认参数、用户提供参数是否清楚分离。
3. 是否编造了图纸没有显示的尺寸、重量、工艺、材料牌号或实时市场价。
4. 必填字段缺失时，是否明确写入“未识别”或“待确认”。
5. 公式、单位、数量、税/未税口径和成本分项是否自洽。
6. 如果缺少材料单价、工艺单价或关键尺寸，是否避免输出确定正式总价。
7. 输出格式是否满足对应 skill 的要求，是否足够给报价/工程人员复核。

判定规则：
- 只有当报告没有关键事实错误、没有编造参数、计算口径自洽、且不确定项已显式标注时，才给 pass。
- 如果报告给出了无法从图纸或用户输入确认的确定价格、尺寸、重量或工艺，必须 fail。
- 如果原始图纸/输入不足以确认，报告可以通过，但前提是它明确标注待确认项，且没有输出伪确定结论。

你只能返回严格 JSON，不要输出 Markdown 或额外解释：
{{
  "verdict": "pass" 或 "fail",
  "confidence": "high" 或 "medium" 或 "low",
  "issues": ["问题1", "问题2"],
  "revision_prompt": "如果 fail，写给生成 Agent 的具体重跑修正要求；如果 pass，留空"
}}
""".strip()

WORK_INSTRUCTIONS = f"""
You are the costing and quotation generation agent for an automated drawing quotation system.
Reply in Simplified Chinese.

Use the user request and the extracted drawing facts as the only source of drawing truth. Do not claim that you can still inspect the original image unless the prompt includes a new image summary.

Available local costing rules:
{REVIEW_SKILL_BLOCKS}

Rules:
- First identify the material category and route to the matching costing rule in your reasoning, but do not mention handoff or tool names as external agents.
- Distinguish clearly between drawing-labeled facts, graphic inference, user-provided values, template/default values, and missing/to-be-confirmed items.
- Do not invent real-time material prices, process prices, dimensions, weights, quantities, plating type, coating boundary, or customer data.
- If key data is missing, list it as to-be-confirmed. You may provide a preliminary estimate only when every assumed/default value is explicitly labeled.
- Do not present a preliminary estimate as a formal final price. If material price, process price, quantity, or critical geometry is missing, write that the formal should-cost cannot be confirmed yet.
- When outputting a cost report, include drawing recognition, weight check, cost parameters, cost breakdown, process details, risks, and to-be-confirmed items.
""".strip()


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_retryable_model_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in TRANSIENT_STATUS_CODES:
        return True

    text = str(exc).lower()
    return "retryable" in text or "bad gateway" in text or "temporarily unavailable" in text


def should_fallback_to_chat_vision(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403, 404, 405}:
        return True

    text = str(exc).lower()
    return "blocked" in text or "not found" in text or "method not allowed" in text


def vision_endpoint_mode() -> str:
    legacy = os.getenv("QUOTE_VISION_USE_RESPONSES")
    if legacy is not None:
        return "responses" if env_flag("QUOTE_VISION_USE_RESPONSES", default=True) else "chat"

    value = os.getenv("QUOTE_VISION_ENDPOINT", "auto").strip().lower()
    return value if value in {"auto", "responses", "chat"} else "auto"


def review_endpoint_mode() -> str:
    value = os.getenv("QUOTE_REVIEW_ENDPOINT", "responses").strip().lower()
    return value if value in {"responses", "chat"} else "responses"


def work_endpoint_mode() -> str:
    value = os.getenv("QUOTE_WORK_ENDPOINT", "chat").strip().lower()
    return value if value in {"responses", "chat"} else "chat"


def work_base_url() -> str | None:
    return os.getenv("QUOTE_WORK_BASE_URL") or os.getenv("QUOTE_REVIEW_BASE_URL") or os.getenv("OPENAI_BASE_URL")


def work_api_key() -> str | None:
    return os.getenv("QUOTE_WORK_API_KEY") or os.getenv("QUOTE_REVIEW_API_KEY") or os.getenv("OPENAI_API_KEY")


def review_base_url() -> str | None:
    return os.getenv("QUOTE_REVIEW_BASE_URL") or os.getenv("OPENAI_BASE_URL")


def review_api_key() -> str | None:
    return os.getenv("QUOTE_REVIEW_API_KEY") or os.getenv("OPENAI_API_KEY")


async def run_with_retries(call: Any, attempts: int = 3) -> Any:
    attempts = env_int("QUOTE_MODEL_RETRY_ATTEMPTS", attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not is_retryable_model_error(exc):
                raise
            await asyncio.sleep(min(2 ** attempt, 10))

    raise RuntimeError(f"model call failed after {attempts} attempts: {last_error}")


async def run_agent_with_retries(agent: Any, agent_input: Any, attempts: int = 3) -> Any:
    return await run_with_retries(lambda: Runner.run(agent, agent_input), attempts=attempts)


@dataclass
class ReviewOutcome:
    verdict: str
    confidence: str
    issues: list[str]
    revision_prompt: str
    raw: str

    @property
    def passed(self) -> bool:
        return self.verdict.lower() == "pass"


def extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("review output did not contain a JSON object")
    return json.loads(match.group(0))


def parse_review(text: str) -> ReviewOutcome:
    try:
        payload = extract_json(text)
    except Exception as exc:
        return ReviewOutcome(
            verdict="fail",
            confidence="low",
            issues=[f"审核 Agent 未返回可解析 JSON：{exc}"],
            revision_prompt="重新生成报告，并确保所有图纸事实、推断、计算公式、待确认项完整清晰。",
            raw=text,
        )

    issues = payload.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]

    return ReviewOutcome(
        verdict=str(payload.get("verdict", "fail")).lower(),
        confidence=str(payload.get("confidence", "unknown")),
        issues=[str(issue) for issue in issues],
        revision_prompt=str(payload.get("revision_prompt", "")).strip(),
        raw=text,
    )


def build_retry_prompt(
    original_prompt: str,
    candidate_report: str,
    review: ReviewOutcome,
    round_number: int,
) -> str:
    issues = "\n".join(f"- {issue}" for issue in review.issues) or "- 未给出具体问题"
    revision_prompt = review.revision_prompt or "请重新识别原始图纸并修正报价报告。"
    return f"""
这是第 {round_number} 轮自动审核后的重跑任务。

原始用户需求：
{original_prompt}

上一版报价报告：
{candidate_report}

审核未通过原因：
{issues}

审核 Agent 的修正要求：
{revision_prompt}

请重新对照“原始用户需求”和“附件识别摘要”修正报告，不要只在上一版文字上做表面修补。
必须修正问题后重新输出完整报告。若图纸或用户输入不足以确认，请把相关项列为待确认，不要输出正式确定价格。
""".strip()


def build_review_prompt(original_prompt: str, candidate_report: str) -> str:
    return f"""
请审核下面这份报价报告是否可以作为正式输出。

原始用户需求：
{original_prompt}

待审核报价报告：
{candidate_report}

请对照原始用户需求、附件识别摘要和对应成本 skill 规则进行审核，并严格按 JSON 返回。
""".strip()


def build_vision_prompt(
    original_prompt: str,
    files: list[Path],
    previous_summary: str | None = None,
    review_feedback: str | None = None,
) -> str:
    file_list = "\n".join(f"- {path.name}" for path in files)
    retry_context = ""
    if previous_summary or review_feedback:
        retry_context = f"""

上一版附件识别摘要：
{previous_summary or "无"}

审核反馈/需要重点复查的问题：
{review_feedback or "无"}

请带着审核反馈重新检查原始附件，修正上一版识别摘要中可能遗漏、误读或表述不清的内容。
"""
    return f"""
请识别并提取这些报价附件中的图纸事实。

原始用户需求：
{original_prompt}

附件列表：
{file_list}
{retry_context}

只输出可用于后续成本计算和审核的结构化事实摘要，不要报价。
""".strip()


def build_prompt_with_vision_context(original_prompt: str, vision_summary: str) -> str:
    return f"""
原始用户需求：
{original_prompt}

附件识别摘要（由 vision model 从原始附件提取）：
{vision_summary}

后续执行规则：
- 后续报价、路由和审核必须基于上面的用户需求和附件识别摘要。
- 不要假装还能直接查看原始附件；如果摘要中没有识别到关键字段，必须列为待确认。
- 严格区分图纸明确标注、由图形推断、用户文字提供、未识别/待确认。
""".strip()


async def extract_vision_context(
    prompt: str,
    files: list[Path],
    vision_model: Any,
    vision_model_name: str,
    previous_summary: str | None = None,
    review_feedback: str | None = None,
) -> str:
    if not files:
        return ""

    vision_prompt = build_vision_prompt(prompt, files, previous_summary, review_feedback)
    endpoint_mode = vision_endpoint_mode()
    logger.info(
        "quote vision start files=%s model=%s endpoint_mode=%s retry=%s",
        len(files),
        vision_model_name,
        endpoint_mode,
        bool(previous_summary or review_feedback),
    )
    log_office_event(
        "quote_vision_agent",
        "vision_started",
        status="running",
        message="quote_vision_agent 开始提取附件事实。",
        metadata={
            "files": len(files),
            "model": vision_model_name,
            "endpoint_mode": endpoint_mode,
            "retry": bool(previous_summary or review_feedback),
        },
    )
    try:
        if endpoint_mode in {"auto", "responses"}:
            try:
                result = await run_with_retries(lambda: create_vision_summary(vision_prompt, files, vision_model_name))
                logger.info("quote vision done via responses chars=%s", len(result))
                log_office_event(
                    "quote_vision_agent",
                    "vision_completed",
                    status="done",
                    message="quote_vision_agent 已完成附件事实提取。",
                    metadata={"endpoint": "responses", "chars": len(result), "files": len(files)},
                )
                return result
            except Exception as exc:
                if endpoint_mode == "responses" or not should_fallback_to_chat_vision(exc):
                    raise
                log_office_event(
                    "quote_vision_agent",
                    "vision_fallback",
                    status="running",
                    message="Responses 识图失败，切换到 chat Agent 继续。",
                    metadata={"from": "responses", "to": "chat"},
                    error=str(exc),
                )
                logger.warning("quote vision responses failed; falling back to chat: %s", exc)

        agent = build_vision_agent(vision_model)
        vision_input = build_agent_input(vision_prompt, files)
        result = await run_agent_with_retries(agent, vision_input)
        output = str(result.final_output)
        logger.info("quote vision done via chat chars=%s", len(output))
        log_office_event(
            "quote_vision_agent",
            "vision_completed",
            status="done",
            message="quote_vision_agent 已完成附件事实提取。",
            metadata={"endpoint": "chat", "chars": len(output), "files": len(files)},
        )
        return output
    except Exception as exc:
        log_office_event(
            "quote_vision_agent",
            "vision_failed",
            status="failed",
            message="quote_vision_agent 附件事实提取失败。",
            metadata={"files": len(files), "endpoint_mode": endpoint_mode},
            error=str(exc),
        )
        raise


async def generate_once(
    prompt: str,
    files: list[Path],
    work_model: Any,
    work_model_name: str | None = None,
) -> str:
    endpoint_mode = work_endpoint_mode()
    model_name = work_model_name or (work_model if isinstance(work_model, str) else os.getenv("QUOTE_WORK_MODEL"))
    logger.info("quote work generation start files=%s endpoint_mode=%s", len(files), endpoint_mode)
    log_office_event(
        "quote_costing_agent",
        "costing_started",
        status="running",
        message="quote_costing_agent 开始生成报价报告。",
        metadata={"files": len(files), "endpoint_mode": endpoint_mode, "model": model_name},
    )
    try:
        if endpoint_mode == "responses":
            if files:
                raise RuntimeError("QUOTE_WORK_ENDPOINT=responses only supports text input after vision extraction.")
            if not model_name:
                raise RuntimeError("Missing work model name for Responses API.")
            result = await run_with_retries(
                lambda: create_text_response(
                    prompt=prompt,
                    model_name=str(model_name),
                    instructions=WORK_INSTRUCTIONS,
                    base_url=work_base_url(),
                    api_key=work_api_key(),
                    stream_env_name="QUOTE_WORK_STREAM",
                )
            )
            logger.info("quote work generation done via responses chars=%s", len(result))
            log_office_event(
                "quote_costing_agent",
                "costing_completed",
                status="done",
                message="quote_costing_agent 已生成报价报告。",
                metadata={"endpoint": "responses", "chars": len(result), "files": len(files)},
            )
            return result

        agent = build_agent_system(work_model)
        agent_input = build_agent_input(prompt, files)
        result = await run_agent_with_retries(agent, agent_input)
        output = str(result.final_output)
        logger.info("quote work generation done chars=%s", len(output))
        log_office_event(
            "quote_costing_agent",
            "costing_completed",
            status="done",
            message="quote_costing_agent 已生成报价报告。",
            metadata={"endpoint": "chat", "chars": len(output), "files": len(files)},
        )
        return output
    except Exception as exc:
        log_office_event(
            "quote_costing_agent",
            "costing_failed",
            status="failed",
            message="quote_costing_agent 报价报告生成失败。",
            metadata={"files": len(files), "endpoint_mode": endpoint_mode, "model": model_name},
            error=str(exc),
        )
        raise


async def review_once(
    original_prompt: str,
    candidate_report: str,
    files: list[Path],
    review_model: Any,
    review_model_name: str,
) -> ReviewOutcome:
    review_prompt = build_review_prompt(original_prompt, candidate_report)
    endpoint_mode = review_endpoint_mode()
    logger.info(
        "quote review start files=%s report_chars=%s model=%s endpoint_mode=%s",
        len(files),
        len(candidate_report),
        review_model_name,
        endpoint_mode,
    )
    log_office_event(
        "quote_review_agent",
        "review_started",
        status="running",
        message="quote_review_agent 开始复核报价报告。",
        metadata={"files": len(files), "report_chars": len(candidate_report), "endpoint_mode": endpoint_mode},
    )
    try:
        if endpoint_mode == "responses":
            result = await run_with_retries(
                lambda: create_text_response(
                    prompt=review_prompt,
                    model_name=review_model_name,
                    instructions=REVIEW_INSTRUCTIONS,
                    base_url=review_base_url(),
                    api_key=review_api_key(),
                    stream_env_name="QUOTE_REVIEW_STREAM",
                )
            )
            logger.info("quote review done via responses chars=%s", len(result))
            outcome = parse_review(result)
            log_office_event(
                "quote_review_agent",
                "review_completed",
                status="done" if outcome.passed else "failed",
                message="quote_review_agent 已完成报价报告复核。",
                metadata={
                    "endpoint": "responses",
                    "verdict": outcome.verdict,
                    "confidence": outcome.confidence,
                    "issues": len(outcome.issues),
                },
            )
            return outcome

        reviewer = build_review_agent(review_model)
        review_input = build_agent_input(review_prompt, files)
        result = await run_agent_with_retries(reviewer, review_input)
        output = str(result.final_output)
        logger.info("quote review done via chat chars=%s", len(output))
        outcome = parse_review(output)
        log_office_event(
            "quote_review_agent",
            "review_completed",
            status="done" if outcome.passed else "failed",
            message="quote_review_agent 已完成报价报告复核。",
            metadata={
                "endpoint": "chat",
                "verdict": outcome.verdict,
                "confidence": outcome.confidence,
                "issues": len(outcome.issues),
            },
        )
        return outcome
    except Exception as exc:
        log_office_event(
            "quote_review_agent",
            "review_failed",
            status="failed",
            message="quote_review_agent 报价报告复核失败。",
            metadata={"files": len(files), "report_chars": len(candidate_report), "endpoint_mode": endpoint_mode},
            error=str(exc),
        )
        raise


async def run_with_quality_loop(
    prompt: str,
    files: list[Path],
    vision_model: Any,
    vision_model_name: str,
    work_model: Any,
    work_model_name: str | None,
    review_model: Any,
    review_model_name: str,
    max_review_rounds: int,
    include_audit: bool = False,
) -> str:
    vision_context = await extract_vision_context(prompt, files, vision_model, vision_model_name)
    working_prompt = build_prompt_with_vision_context(prompt, vision_context) if vision_context else prompt
    working_files: list[Path] = []

    candidate = await generate_once(working_prompt, working_files, work_model, work_model_name)
    audit_log: list[ReviewOutcome] = []

    for round_index in range(1, max_review_rounds + 1):
        review = await review_once(working_prompt, candidate, working_files, review_model, review_model_name)
        audit_log.append(review)
        if review.passed:
            if not include_audit:
                return candidate
            return append_audit_summary(candidate, audit_log)

        if files:
            feedback = "\n".join(f"- {issue}" for issue in review.issues)
            if review.revision_prompt:
                feedback = f"{feedback}\n\n修正要求：\n{review.revision_prompt}".strip()
            vision_context = await extract_vision_context(
                prompt,
                files,
                vision_model,
                vision_model_name,
                previous_summary=vision_context,
                review_feedback=feedback,
            )
            working_prompt = build_prompt_with_vision_context(prompt, vision_context)

        retry_prompt = build_retry_prompt(working_prompt, candidate, review, round_index + 1)
        candidate = await generate_once(retry_prompt, working_files, work_model, work_model_name)

    final_review = await review_once(working_prompt, candidate, working_files, review_model, review_model_name)
    audit_log.append(final_review)
    if final_review.passed:
        if not include_audit:
            return candidate
        return append_audit_summary(candidate, audit_log)

    return unverified_report(candidate, audit_log)


def append_audit_summary(report: str, audit_log: list[ReviewOutcome]) -> str:
    latest = audit_log[-1]
    return f"""{report}

---

## 自动审核记录

- 审核结论：通过
- 审核轮次：{len(audit_log)}
- 置信等级：{latest.confidence}
"""


def unverified_report(candidate: str, audit_log: list[ReviewOutcome]) -> str:
    latest = audit_log[-1]
    issues = "\n".join(f"- {issue}" for issue in latest.issues) or "- 审核未给出具体问题"
    return f"""# 自动审核未通过

这份结果没有作为正式报价输出。请先处理以下问题后重新运行。

## 最新审核问题

{issues}

## 最后一版草稿

> 注意：以下内容仅供排查，不可作为正式报价。

{candidate}
"""
