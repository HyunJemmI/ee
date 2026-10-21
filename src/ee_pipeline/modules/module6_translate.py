"""M6: translate classified E/E requirements with the ChatT API."""

from __future__ import annotations

import asyncio
import random
import re
import time

import httpx

from ..chatt_client import request_chat_completion, request_chat_completion_sync, validate_chatt_config
from ..config import CHATT_MODEL, TRANSLATION_PROMPT, TRANSLATION_SYSTEM_PROMPT, PipelineConfig
from ..io_utils import AsyncRequestPacer, write_jsonl
from ..models import ClassificationResult, TranslationResult


def _build_translation_payload(
    text: str,
    *,
    model: str = CHATT_MODEL,
    system_prompt: str = TRANSLATION_SYSTEM_PROMPT,
    prompt_template: str = TRANSLATION_PROMPT,
) -> dict:
    """Build an OpenAI-compatible ChatT chat-completions request."""
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt_template.format(text=text),
            },
        ],
        "temperature": 0.0,
        "max_tokens": 1000,
    }


def _is_usable_translation(text: str) -> bool:
    return bool(text and len(text.strip()) > 5)


def _retry_after_seconds(error: Exception) -> float | None:
    """Read Retry-After or the delay suggested in a rate-limit error."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    match = re.search(r"try again in\s+([\d.]+)s", str(error), flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


async def _translate_single_async(
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
    config: PipelineConfig | None = None,
    pacer: AsyncRequestPacer | None = None,
) -> str:
    """Translate once through ChatT."""
    settings = config or PipelineConfig()
    validate_chatt_config(settings)
    payload = _build_translation_payload(
        text,
        model=settings.chatt_model,
        system_prompt=settings.translation_system_prompt,
        prompt_template=settings.translation_prompt,
    )
    if pacer is not None:
        await pacer.wait()

    if client is not None:
        return await request_chat_completion(client, settings, payload)
    async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as owned_client:
        return await request_chat_completion(owned_client, settings, payload)


async def _translate_with_retry(
    text: str,
    max_retries: int = 3,
    *,
    client: httpx.AsyncClient | None = None,
    config: PipelineConfig | None = None,
    label: str = "",
    pacer: AsyncRequestPacer | None = None,
) -> tuple[str, bool]:
    """Retry one translation and return text plus success state."""
    attempts = max(1, max_retries)
    last_error = "unknown error"
    for attempt in range(attempts):
        try:
            translated = await _translate_single_async(text, client=client, config=config, pacer=pacer)
            return translated, True
        except Exception as error:
            last_error = str(error)
            if attempt + 1 < attempts:
                wait_time = _retry_after_seconds(error)
                if wait_time is None:
                    wait_time = (2**attempt) + random.uniform(0, 1)
                else:
                    wait_time += random.uniform(0.25, 0.75)
                print(f"       [M6] Retry {attempt + 1}/{max_retries} for {label} after {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
    print(f"       [M6] FAILED [{label}]: {last_error}")
    return "", False


def _translation_result(classification: ClassificationResult, text: str, success: bool) -> TranslationResult:
    usable = success and _is_usable_translation(text)
    return TranslationResult(
        block_id=classification.block_id,
        doc_id=classification.doc_id,
        cur_id=classification.cur_id,
        original_text=classification.original_content,
        translated_text=text if success else "",
        status="OK" if usable else "REVIEW_NEEDED",
    )


async def translate_batch_parallel(
    classifications: list[ClassificationResult],
    max_concurrency: int = 4,
    *,
    config: PipelineConfig | None = None,
) -> list[TranslationResult]:
    """Translate only requirement + E/E classification intersections."""
    settings = config or PipelineConfig()
    selected = [item for item in classifications if item.is_requirement and item.is_ee_related]
    print(f"[M6] Translating {len(selected)} EE requirements...")
    if not selected:
        return []
    validate_chatt_config(settings)
    pacer = AsyncRequestPacer(settings.chatt_requests_per_minute)

    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
        async def translate_one(item: ClassificationResult) -> tuple[str, bool]:
            async with semaphore:
                return await _translate_with_retry(
                    item.original_content,
                    settings.max_retries,
                    client=client,
                    config=settings,
                    label=item.block_id,
                    pacer=pacer,
                )

        raw_results = await asyncio.gather(
            *(translate_one(item) for item in selected),
            return_exceptions=True,
        )

    results: list[TranslationResult] = []
    failures: list[str] = []
    for item, raw_result in zip(selected, raw_results, strict=True):
        if isinstance(raw_result, Exception):
            failures.append(f"{item.block_id}: {raw_result}")
        else:
            translated, success = raw_result
            if not success:
                failures.append(f"{item.block_id}: translation failed after retries")
                continue
            result = _translation_result(item, translated, success)
            if result.status == "REVIEW_NEEDED":
                failures.append(f"{item.block_id}: unusable translation")
                continue
            results.append(result)
    if failures:
        sample = "; ".join(failures[:5])
        raise RuntimeError(
            f"ChatT translation failed for {len(failures)} record(s); "
            f"no translation artifact was written. Examples: {sample}"
        )
    return results


def _translate_single(text: str, config: PipelineConfig | None = None) -> str:
    """Synchronous one-shot compatibility helper."""
    settings = config or PipelineConfig()
    validate_chatt_config(settings)
    payload = _build_translation_payload(
        text,
        model=settings.chatt_model,
        system_prompt=settings.translation_system_prompt,
        prompt_template=settings.translation_prompt,
    )
    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True) as client:
        return request_chat_completion_sync(client, settings, payload)


def translate_batch(
    classifications: list[ClassificationResult],
    config: PipelineConfig | None = None,
) -> list[TranslationResult]:
    """Synchronous compatibility wrapper with per-item error isolation."""
    settings = config or PipelineConfig()
    return asyncio.run(translate_batch_parallel(classifications, settings.max_concurrency, config=settings))


async def run(config: PipelineConfig, classifications: list[ClassificationResult]) -> list[TranslationResult]:
    """Execute M6 and persist ``translated.jsonl``."""
    config.prepare_run_dir()
    started = time.perf_counter()
    results = await translate_batch_parallel(classifications, config.max_concurrency, config=config)
    elapsed = time.perf_counter() - started
    path = write_jsonl(config.run_dir / "translated.jsonl", results)
    ok_count = sum(result.status == "OK" for result in results)
    print(f"[M6] {len(results)} translated | OK: {ok_count} | Review needed: {len(results) - ok_count}")
    print(f"[M6] Elapsed: {elapsed:.2f}s | {path}")
    return results
