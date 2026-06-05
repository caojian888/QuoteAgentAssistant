from __future__ import annotations

import os
from pathlib import Path

from .base_url import normalize_base_url
from .io import build_agent_input
from .responses_text import create_streaming_response, env_flag, env_float, response_text

import httpx


VISION_INSTRUCTIONS = """
你是报价系统的图纸识别 Agent，只负责把图片、PDF 或附件中的可见事实提取成结构化文字，不直接报价。

输出要求：
- 默认使用中文。
- 严格区分“图纸明确标注”“由图形推断”“用户文字提供”“未识别/待确认”。
- 不编造尺寸、材料、数量、重量、工艺、表面处理、实时价格或客户信息。
- 如果图纸模糊、遮挡、分辨率不足或关键尺寸缺失，必须列为待确认。
- 对每个附件分别说明文件名、可能品类、关键尺寸、材料/牌号、厚度/截面、孔位、折弯/焊接/压接/表面处理、数量、图号/版本、单位和疑点。
- 最后给出“后续报价建议路由”，只能从铜铝排、铜编织线、绝缘纸、大六角螺栓、钣金件、无法确认中选择。
""".strip()


def api_key() -> str:
    key = os.getenv("QUOTE_VISION_API_KEY") or os.getenv("QUOTE_REVIEW_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("缺少 QUOTE_VISION_API_KEY / QUOTE_REVIEW_API_KEY / OPENAI_API_KEY。")
    return key


def responses_endpoint() -> str:
    base_url = (
        os.getenv("QUOTE_VISION_BASE_URL")
        or os.getenv("QUOTE_REVIEW_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
    )
    normalized = normalize_base_url(base_url)
    if not normalized:
        normalized = "https://api.openai.com/v1"
    return f"{normalized.rstrip('/')}/responses"


async def create_vision_summary(prompt: str, files: list[Path], model_name: str) -> str:
    payload = {
        "model": model_name,
        "instructions": VISION_INSTRUCTIONS,
        "input": build_agent_input(prompt, files),
    }
    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0 quote-agent-assistant",
    }

    async with httpx.AsyncClient(timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0)) as client:
        if env_flag("QUOTE_VISION_STREAM", default=True):
            return await create_streaming_response(client, responses_endpoint(), payload, headers)
        response = await client.post(responses_endpoint(), headers=headers, json=payload)

    if response.status_code >= 400:
        raise RuntimeError(f"Responses API error {response.status_code}: {response.text[:1200]}")

    return response_text(response.json())
