"""M6: Translate & Validate — translates EE requirements to Korean via ChatT API.

Translates only the EE-related requirement statements (intersection of M5 axes).
Supports parallel processing with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import CHATT_API_KEY, CHATT_BASE_URL, CHATT_MODEL, PipelineConfig
from ..models import ClassificationResult, TranslationResult


def _build_translation_payload(text: str) -> dict:
    """Build the API request payload for translation."""
    prompt = f"""Translate the following automotive E/E requirement statement from English to Korean.
Maintain technical accuracy and use standard automotive industry terminology.

Original: {text}

Provide only the Korean translation, nothing else. Do not add explanations or notes."""

    return {
        "model": CHATT_MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional automotive technical translator. Translate English to Korean with precise technical terminology."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }


async def _translate_single_async(text: str) -> str:
    """Call ChatT API for a single translation (async version)."""
    payload = _build_translation_payload(text)

    url = f"{CHATT_BASE_URL.rstrip('/')}/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {CHATT_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("No choices in LLM response")

    content = choices[0].get("message", {}).get("content")
    if content is None:
        raise ValueError("LLM returned None content")
    return content.strip()


async def _translate_with_retry(text: str, max_retries: int = 3) -> tuple[str, bool]:
    """Call ChatT API with retry logic.

    Returns:
        (translated_text, success)
    """
    for attempt in range(max_retries):
        try:
            translated = await _translate_single_async(text)
            return translated, True
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"       [M6] Retry {attempt+1}/{max_retries} after {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            else:
                return "", False


async def translate_batch_parallel(
    classifications: list[ClassificationResult],
    max_concurrency: int = 8,
) -> list[TranslationResult]:
    """Translate EE-related requirement statements in parallel.

    Only translates blocks where is_requirement=True AND is_ee_related=True.

    Args:
        classifications: List of ClassificationResult from M5.
        max_concurrency: Maximum number of parallel API calls (default: 4).
                         Increase to 8 after confirming stability.

    Returns:
        List of TranslationResult objects.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[TranslationResult] = []
    to_translate = [c for c in classifications if c.is_requirement and c.is_ee_related]

    print(f"[M6] Translating {len(to_translate)} EE requirements...")

    # All tasks share the semaphore — bounded concurrency handles everything
    async def translate_one(cls: ClassificationResult):
        async with semaphore:
            return await _translate_with_retry(cls.original_content)

    tasks = [translate_one(cls) for cls in to_translate]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, (result, success) in enumerate(batch_results):
        cls = to_translate[i]

        if isinstance(result, Exception) or not success:
            error_msg = str(result) if isinstance(result, Exception) else "Unknown error"
            print(f"       [M6] FAILED [{cls.block_id}]: {error_msg}")
            results.append(TranslationResult(
                block_id=cls.block_id,
                doc_id=cls.doc_id,
                original_text=cls.original_content,
                translated_text="",
                status="REVIEW_NEEDED",
            ))
        else:
            status = "OK" if result and len(result) > 5 else "REVIEW_NEEDED"

            translation_result = TranslationResult(
                block_id=cls.block_id,
                doc_id=cls.doc_id,
                original_text=cls.original_content,
                translated_text=result,
                status=status,
            )
            results.append(translation_result)

            if status == "REVIEW_NEEDED":
                print(f"       WARNING: Translation may need review")

    # Sort results by block_id to maintain original order
    results.sort(key=lambda x: x.block_id)

    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def translate_batch(classifications: list[ClassificationResult]) -> list[TranslationResult]:
    """Translate EE-related requirement statements (sequential, for compatibility).

    Only translates blocks where is_requirement=True AND is_ee_related=True.

    Args:
        classifications: List of ClassificationResult from M5.

    Returns:
        List of TranslationResult objects.
    """
    results: list[TranslationResult] = []
    to_translate = [c for c in classifications if c.is_requirement and c.is_ee_related]

    print(f"[M6] Translating {len(to_translate)} EE requirements...")

    for i, cls in enumerate(to_translate):
        print(f"[M6] Translating [{cls.block_id}] ({i+1}/{len(to_translate)})...")

        try:
            translated = _translate_single(cls.original_content)

            status = "OK" if translated and len(translated) > 5 else "REVIEW_NEEDED"

            result = TranslationResult(
                block_id=cls.block_id,
                doc_id=cls.doc_id,
                original_text=cls.original_content,
                translated_text=translated,
                status=status,
            )
            results.append(result)

            if status == "REVIEW_NEEDED":
                print(f"       WARNING: Translation may need review")

        except Exception as e:
            print(f"       FAILED: {e}")
            results.append(TranslationResult(
                block_id=cls.block_id,
                doc_id=cls.doc_id,
                original_text=cls.original_content,
                translated_text="",
                status="REVIEW_NEEDED",
            ))

    return results


def _translate_single(text: str) -> str:
    """Call ChatT API for a single translation (sync version)."""
    payload = _build_translation_payload(text)

    url = f"{CHATT_BASE_URL.rstrip('/')}/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {CHATT_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("No choices in LLM response")

    content = choices[0].get("message", {}).get("content")
    if content is None:
        raise ValueError("LLM returned None content")
    return content.strip()


async def run(config: PipelineConfig, classifications: list[ClassificationResult]) -> list[TranslationResult]:
    """Execute M6: translate EE requirements.

    Uses parallel processing with bounded concurrency.
    Returns list of TranslationResult objects.
    Writes translated.jsonl to output.
    """
    import time as _time
    t0 = _time.time()
    results = await translate_batch_parallel(classifications, max_concurrency=8)
    m6_elapsed = _time.time() - t0

    translated_path = config.run_dir / "translated.jsonl"
    with open(translated_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(r.model_dump_json() + "\n")

    ok_count = sum(1 for r in results if r.status == "OK")
    review_count = sum(1 for r in results if r.status == "REVIEW_NEEDED")

    print(f"[M6] {len(results)} translated | OK: {ok_count} | Review needed: {review_count}")
    print(f"[M6] Elapsed: {m6_elapsed:.2f}s ({m6_elapsed/max(len(results),1)*1000:.0f}ms/batch)")
    return results
