"""M5: classify requirement candidates with the ChatT API."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from ..chatt_client import request_chat_completion, validate_chatt_config
from ..config import CHATT_MODEL, CLASSIFICATION_PROMPT, CLASSIFICATION_SYSTEM_PROMPT, PipelineConfig
from ..io_utils import AsyncRequestPacer, write_jsonl
from ..models import ClassificationResult, EECategory

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_requirement": {"type": "boolean"},
        "is_ee_related": {"type": "boolean"},
        "ee_category": {
            "type": "string",
            "enum": [category.value for category in EECategory],
        },
        "reasoning": {"type": "string"},
    },
    "required": ["is_requirement", "is_ee_related", "ee_category", "reasoning"],
    "additionalProperties": False,
}


def _build_classification_payload(
    text: str,
    heading: str | None = None,
    *,
    model: str = CHATT_MODEL,
    system_prompt: str = CLASSIFICATION_SYSTEM_PROMPT,
    prompt_template: str = CLASSIFICATION_PROMPT,
) -> dict[str, Any]:
    """Build an OpenAI-compatible ChatT chat-completions request."""
    heading_context = f"Heading: {heading}\n\n" if heading else ""
    prompt = prompt_template.format(heading_context=heading_context, text=text)
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
    }


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse plain, prefixed, or fenced JSON for compatibility and tests."""
    if response_text is None:
        raise ValueError("ChatT returned no text")
    text = response_text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    start = text.find("{")
    if start < 0:
        raise ValueError("ChatT response did not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                except json.JSONDecodeError as error:
                    raise ValueError("ChatT response contained invalid JSON") from error
                if not isinstance(payload, dict):
                    raise ValueError("ChatT JSON response must be an object")
                return payload
    raise ValueError("ChatT response contained an incomplete JSON object")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1"}:
            return True
        if normalized in {"false", "no", "n", "0"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"ChatT returned an invalid boolean value: {value!r}")


def _category(value: Any) -> EECategory:
    if isinstance(value, EECategory):
        return value
    normalized = str(value).strip().casefold()
    for category in EECategory:
        if category.value.casefold() == normalized:
            return category
    raise ValueError(f"ChatT returned an unknown E/E category: {value!r}")


def _validate_classification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the model response without silently inventing classifications."""
    missing = [field for field in ("is_requirement", "is_ee_related") if field not in payload]
    if missing:
        raise ValueError(f"ChatT classification omitted required field(s): {', '.join(missing)}")

    is_requirement = _as_bool(payload["is_requirement"])
    is_ee_related = _as_bool(payload["is_ee_related"])
    category_value = payload.get("ee_category", EECategory.OTHER.value)
    category = _category(category_value)
    if is_ee_related and "ee_category" not in payload:
        raise ValueError("ChatT classification omitted ee_category for an E/E record")

    return {
        "is_requirement": is_requirement,
        "is_ee_related": is_ee_related,
        "ee_category": category,
        "reasoning": str(payload.get("reasoning", "")).strip(),
    }


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


async def _classify_single_async(
    text: str,
    heading: str | None = None,
    segment_id: str = "",
    batch_id: str = "",
    retry_count: int = 0,
    *,
    client: httpx.AsyncClient | None = None,
    config: PipelineConfig | None = None,
    pacer: AsyncRequestPacer | None = None,
) -> dict[str, Any]:
    """Call ChatT once and parse the structured response."""
    settings = config or PipelineConfig()
    validate_chatt_config(settings)
    payload = _build_classification_payload(
        text,
        heading,
        model=settings.chatt_model,
        system_prompt=settings.classification_system_prompt,
        prompt_template=settings.classification_prompt,
    )
    if pacer is not None:
        await pacer.wait()

    if client is not None:
        output = await request_chat_completion(client, settings, payload)
    else:
        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as owned_client:
            output = await request_chat_completion(owned_client, settings, payload)
    return _validate_classification_payload(_parse_llm_response(output))


async def _classify_with_retry(
    text: str,
    heading: str | None = None,
    segment_id: str = "",
    batch_id: str = "",
    max_retries: int = 3,
    *,
    client: httpx.AsyncClient | None = None,
    config: PipelineConfig | None = None,
    pacer: AsyncRequestPacer | None = None,
) -> tuple[dict[str, Any], bool]:
    """Retry one ChatT request with bounded exponential backoff."""
    attempts = max(1, max_retries)
    last_error = "unknown error"
    for attempt in range(attempts):
        try:
            result = await _classify_single_async(
                text,
                heading,
                segment_id,
                batch_id,
                attempt,
                client=client,
                config=config,
                pacer=pacer,
            )
            return result, True
        except Exception as error:
            last_error = str(error)
            if attempt + 1 < attempts:
                wait_time = _retry_after_seconds(error)
                if wait_time is None:
                    wait_time = (2**attempt) + random.uniform(0, 1)
                else:
                    wait_time += random.uniform(0.25, 0.75)
                print(f"       [M5] Retry {attempt + 1}/{max_retries} for {segment_id or batch_id} after {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
    return {"error": last_error}, False


def _batch_heading(batch: Mapping[str, Any]) -> str | None:
    if batch.get("heading"):
        return str(batch["heading"])
    source_segments = batch.get("source_segments") or []
    if source_segments:
        return source_segments[0].get("heading_text") or source_segments[0].get("heading")
    return None


def _batch_input_text(batch: Mapping[str, Any]) -> str:
    """Read M4.5 input first while retaining compatibility with raw M4 batches."""
    return str(batch.get("llm_text") or batch.get("merged_text") or batch.get("original_text") or "")


def _batch_original_text(batch: Mapping[str, Any]) -> str:
    return str(batch.get("original_text") or batch.get("merged_text") or batch.get("llm_text") or "")


async def classify_batch_parallel(
    batches: list[Mapping[str, Any]],
    max_concurrency: int = 4,
    *,
    config: PipelineConfig | None = None,
) -> list[ClassificationResult]:
    """Classify batches concurrently while preserving input order."""
    settings = config or PipelineConfig()
    concurrency = max(1, max_concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    to_classify = [batch for batch in batches if not batch.get("skip_candidate", False)]
    skipped_count = len(batches) - len(to_classify)
    print(f"[M5] {len(batches)} total batches, {skipped_count} skip_candidate skipped, {len(to_classify)} to classify")
    if not to_classify:
        return []
    validate_chatt_config(settings)
    pacer = AsyncRequestPacer(settings.chatt_requests_per_minute)

    async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
        async def classify_one(index: int, batch: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
            async with semaphore:
                return await _classify_with_retry(
                    _batch_input_text(batch),
                    _batch_heading(batch),
                    str(batch.get("batch_id", f"batch_{index + 1:04d}")),
                    str(batch.get("batch_id", "")),
                    settings.max_retries,
                    client=client,
                    config=settings,
                    pacer=pacer,
                )

        raw_results = await asyncio.gather(
            *(classify_one(index, batch) for index, batch in enumerate(to_classify)),
            return_exceptions=True,
        )

    results: list[ClassificationResult] = []
    failures: list[str] = []
    for index, (batch, raw_result) in enumerate(zip(to_classify, raw_results, strict=True)):
        batch_id = str(batch.get("batch_id", f"batch_{index + 1:04d}"))
        doc_id = str(batch.get("doc_id") or batch.get("document_id") or "unknown")
        cur_id = str(batch.get("cur_id") or "") or None
        original = _batch_original_text(batch)
        if isinstance(raw_result, Exception):
            payload, success = {"error": str(raw_result)}, False
        else:
            payload, success = raw_result

        if not success:
            error = payload.get("error", "unknown error")
            print(f"       [M5] FAILED batch {batch_id}: {error}")
            failures.append(f"{batch_id}: {error}")
            continue

        result = ClassificationResult(
            block_id=batch_id,
            doc_id=doc_id,
            cur_id=cur_id,
            is_requirement=_as_bool(payload.get("is_requirement")),
            is_ee_related=_as_bool(payload.get("is_ee_related")),
            ee_category=_category(payload.get("ee_category", EECategory.OTHER.value)),
            reasoning=str(payload.get("reasoning", "")),
            original_content=original,
        )
        results.append(result)
        req = "REQ" if result.is_requirement else "non-REQ"
        ee = "EE" if result.is_ee_related else "non-EE"
        print(f"       → {req} / {ee} | {result.ee_category.value if result.ee_category else EECategory.OTHER.value}")
    if failures:
        sample = "; ".join(failures[:5])
        raise RuntimeError(
            f"ChatT classification failed for {len(failures)} record(s); "
            f"no classification artifact was written. Examples: {sample}"
        )
    return results


def classify_batch(
    batches: list[Mapping[str, Any]],
    config: PipelineConfig | None = None,
) -> list[ClassificationResult]:
    """Synchronous compatibility wrapper for standalone CLI use."""
    settings = config or PipelineConfig()
    return asyncio.run(classify_batch_parallel(batches, settings.max_concurrency, config=settings))


async def run(config: PipelineConfig, batches: list[Mapping[str, Any]]) -> list[ClassificationResult]:
    """Execute M5 and persist ``classifications.jsonl``."""
    config.prepare_run_dir()
    started = time.perf_counter()
    results = await classify_batch_parallel(batches, config.max_concurrency, config=config)
    elapsed = time.perf_counter() - started
    path = write_jsonl(config.run_dir / "classifications.jsonl", results)
    req_count = sum(result.is_requirement for result in results)
    ee_count = sum(result.is_ee_related for result in results)
    both_count = sum(result.is_requirement and result.is_ee_related for result in results)
    print(f"[M5] {len(results)} classified | REQ: {req_count} | EE: {ee_count} | EE-REQ: {both_count}")
    print(f"[M5] Elapsed: {elapsed:.2f}s | {path}")
    return results
