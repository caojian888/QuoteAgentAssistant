from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx


class FeishuAuthError(RuntimeError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 1.0)
    except ValueError:
        return default


def feishu_app_id() -> str:
    return os.getenv("QUOTE_FEISHU_APP_ID", "").strip()


def feishu_app_secret() -> str:
    return os.getenv("QUOTE_FEISHU_APP_SECRET", "").strip()


def feishu_login_enabled() -> bool:
    return _env_bool("QUOTE_FEISHU_LOGIN_ENABLED", False) and bool(feishu_app_id() and feishu_app_secret())


def feishu_default_role() -> str:
    return os.getenv("QUOTE_FEISHU_DEFAULT_ROLE", "user").strip() or "user"


def feishu_accounts_base_url() -> str:
    return os.getenv("QUOTE_FEISHU_ACCOUNTS_BASE_URL", "https://accounts.feishu.cn").strip().rstrip("/")


def feishu_openapi_base_url() -> str:
    return os.getenv("QUOTE_FEISHU_OPENAPI_BASE_URL", "https://open.feishu.cn").strip().rstrip("/")


def build_feishu_authorize_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": feishu_app_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    scope = os.getenv("QUOTE_FEISHU_SCOPES", "").strip()
    if scope:
        params["scope"] = scope
    return f"{feishu_accounts_base_url()}/open-apis/authen/v1/authorize?{urlencode(params)}"


def _data_or_raise(payload: dict[str, Any], action: str) -> dict[str, Any]:
    code = payload.get("code", 0)
    if code not in {0, "0", None}:
        message = payload.get("msg") or payload.get("message") or f"Feishu {action} failed."
        raise FeishuAuthError(str(message))
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


async def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    timeout = _env_float("QUOTE_FEISHU_TIMEOUT_SECONDS", 12.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers or {})
        response.raise_for_status()
        return response.json()


async def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    timeout = _env_float("QUOTE_FEISHU_TIMEOUT_SECONDS", 12.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_app_access_token() -> str:
    payload = await _post_json(
        f"{feishu_openapi_base_url()}/open-apis/auth/v3/app_access_token/internal",
        {"app_id": feishu_app_id(), "app_secret": feishu_app_secret()},
    )
    data = _data_or_raise(payload, "app access token")
    token = data.get("app_access_token") or payload.get("app_access_token")
    if not token:
        raise FeishuAuthError("Feishu app_access_token is missing.")
    return str(token)


async def get_user_access_token(code: str, app_access_token: str) -> str:
    payload = await _post_json(
        f"{feishu_openapi_base_url()}/open-apis/authen/v1/oidc/access_token",
        {"grant_type": "authorization_code", "code": code},
        headers={"Authorization": f"Bearer {app_access_token}"},
    )
    data = _data_or_raise(payload, "user access token")
    token = data.get("access_token") or data.get("user_access_token") or payload.get("access_token")
    if not token:
        raise FeishuAuthError("Feishu user access token is missing.")
    return str(token)


async def get_user_info(user_access_token: str) -> dict[str, Any]:
    payload = await _get_json(
        f"{feishu_openapi_base_url()}/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {user_access_token}"},
    )
    data = _data_or_raise(payload, "user info")
    if not data:
        raise FeishuAuthError("Feishu user info is empty.")
    return data


async def fetch_feishu_user_profile(code: str) -> dict[str, Any]:
    app_access_token = await get_app_access_token()
    user_access_token = await get_user_access_token(code, app_access_token)
    return await get_user_info(user_access_token)
