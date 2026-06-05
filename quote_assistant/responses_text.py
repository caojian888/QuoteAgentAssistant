from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base_url import normalize_base_url


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def response_text(response: Any) -> str:
    if isinstance(response, dict):
        output_text = response.get("output_text")
        if output_text:
            return str(output_text)

        parts: list[str] = []
        for item in response.get("output") or []:
            for content in item.get("content") or []:
                text = content.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts) if parts else str(response)

    return str(response)


def responses_endpoint(base_url: str | None) -> str:
    normalized = normalize_base_url(base_url)
    if not normalized:
        normalized = "https://api.openai.com/v1"
    return f"{normalized.rstrip('/')}/responses"


async def create_streaming_response(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> str:
    stream_payload = {**payload, "stream": True}
    parts: list[str] = []
    completed_response: Any = None

    async with client.stream("POST", endpoint, headers=headers, json=stream_payload) as response:
        if response.status_code >= 400:
            body = await response.aread()
            text = body.decode("utf-8", errors="replace")
            raise RuntimeError(f"Responses API error {response.status_code}: {text[:1200]}")

        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue

            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue

            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                parts.append(str(event.get("delta", "")))
            elif event_type == "response.completed":
                completed_response = event.get("response")

    if parts:
        return "".join(parts)
    if completed_response is not None:
        return response_text(completed_response)
    return ""


async def create_text_response(
    prompt: str,
    model_name: str,
    instructions: str,
    base_url: str | None,
    api_key: str | None,
    stream_env_name: str,
) -> str:
    if not api_key:
        raise RuntimeError("缺少 Responses API key。")

    endpoint = responses_endpoint(base_url)
    payload = {
        "model": model_name,
        "instructions": instructions,
        "input": prompt,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0 quote-agent-assistant",
    }

    async with httpx.AsyncClient(timeout=env_float("QUOTE_MODEL_TIMEOUT_SECONDS", 90.0)) as client:
        if env_flag(stream_env_name, default=True):
            return await create_streaming_response(client, endpoint, payload, headers)
        response = await client.post(endpoint, headers=headers, json=payload)

    if response.status_code >= 400:
        raise RuntimeError(f"Responses API error {response.status_code}: {response.text[:1200]}")

    return response_text(response.json())
