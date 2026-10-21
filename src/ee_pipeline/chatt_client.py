"""Small OpenAI-compatible ChatT chat-completions adapter."""

from __future__ import annotations

from typing import Any

import httpx

from .config import PipelineConfig


def chat_completions_url(base_url: str) -> str:
    """Accept a server root, a ``/v1`` root, or the complete endpoint."""
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("CHATT_BASE_URL is not configured")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def validate_chatt_config(config: PipelineConfig) -> None:
    """Fail before processing when required ChatT settings are absent."""
    missing = [
        name
        for name, value in (
            ("CHATT_API_KEY", config.chatt_api_key),
            ("CHATT_BASE_URL", config.chatt_base_url),
            ("CHATT_MODEL", config.chatt_model),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing ChatT configuration: {', '.join(missing)}")
    if config.max_concurrency < 1:
        raise ValueError("CHATT_MAX_CONCURRENCY must be at least 1")
    if config.chatt_requests_per_minute < 1:
        raise ValueError("CHATT_REQUESTS_PER_MINUTE must be at least 1")


def authorization_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def extract_message_content(payload: dict[str, Any]) -> str:
    """Extract text from common OpenAI-compatible ChatT response shapes."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("ChatT response did not contain choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("ChatT response choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("ChatT response did not contain a message")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text"}
        ).strip()
        if text:
            return text
    raise ValueError("ChatT response message contained no text")


async def request_chat_completion(
    client: httpx.AsyncClient,
    config: PipelineConfig,
    payload: dict[str, Any],
) -> str:
    response = await client.post(
        chat_completions_url(config.chatt_base_url),
        json=payload,
        headers=authorization_headers(config.chatt_api_key),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("ChatT response body is not a JSON object")
    return extract_message_content(data)


def request_chat_completion_sync(
    client: httpx.Client,
    config: PipelineConfig,
    payload: dict[str, Any],
) -> str:
    response = client.post(
        chat_completions_url(config.chatt_base_url),
        json=payload,
        headers=authorization_headers(config.chatt_api_key),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("ChatT response body is not a JSON object")
    return extract_message_content(data)
