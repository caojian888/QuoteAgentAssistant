from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

from .base_url import normalize_base_url


@dataclass(frozen=True)
class ModelConfig:
    vision_model: Any
    work_model: Any
    review_model: Any
    vision_model_label: str
    work_model_label: str
    review_model_label: str


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def openai_compatible_model(
    model_name: str,
    base_url: str | None,
    api_key: str | None,
    label: str,
) -> Any:
    if not base_url:
        return model_name

    if not api_key:
        raise RuntimeError(f"配置了 {label}_BASE_URL，但缺少 {label}_API_KEY。")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=normalize_base_url(base_url),
        timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0),
    )
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)


def build_model_config(
    work_model_override: str | None = None,
    vision_model_override: str | None = None,
    review_model_override: str | None = None,
) -> ModelConfig:
    if env_flag("QUOTE_DISABLE_TRACING", default=False):
        set_tracing_disabled(disabled=True)

    work_model_name = (
        work_model_override
        or os.getenv("QUOTE_WORK_MODEL")
        or os.getenv("QUOTE_AGENT_MODEL")
        or "gpt-4.1-mini"
    )
    review_model_name = (
        review_model_override
        or os.getenv("QUOTE_REVIEW_MODEL")
        or os.getenv("QUOTE_AGENT_MODEL")
        or "gpt-4.1"
    )
    vision_model_name = (
        vision_model_override
        or os.getenv("QUOTE_VISION_MODEL")
        or review_model_name
    )

    work_model = openai_compatible_model(
        model_name=work_model_name,
        base_url=os.getenv("QUOTE_WORK_BASE_URL"),
        api_key=os.getenv("QUOTE_WORK_API_KEY"),
        label="QUOTE_WORK",
    )

    vision_model = openai_compatible_model(
        model_name=vision_model_name,
        base_url=(
            os.getenv("QUOTE_VISION_BASE_URL")
            or os.getenv("QUOTE_REVIEW_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
        ),
        api_key=(
            os.getenv("QUOTE_VISION_API_KEY")
            or os.getenv("QUOTE_REVIEW_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
        label="QUOTE_VISION",
    )

    review_model = openai_compatible_model(
        model_name=review_model_name,
        base_url=os.getenv("QUOTE_REVIEW_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("QUOTE_REVIEW_API_KEY") or os.getenv("OPENAI_API_KEY"),
        label="QUOTE_REVIEW",
    )

    return ModelConfig(
        vision_model=vision_model,
        work_model=work_model,
        review_model=review_model,
        vision_model_label=vision_model_name,
        work_model_label=work_model_name,
        review_model_label=review_model_name,
    )
