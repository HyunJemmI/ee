"""M5: AI Classifier — 2-axis classification via ChatT API.

Classifies each requirement candidate on two axes:
1. is_requirement — Is this a requirement statement?
2. is_ee_related — Is this related to electrical/electronic systems?

Uses the ChatT API (ChatT-VIBE model) for classification.
Supports parallel processing with bounded concurrency and metadata filtering.

Input format (from M4):
  Each item is a batch dict with:
    - merged_text: the text to classify
    - source_segments: list of original segment info
    - batch_id: batch identifier
    - skip_candidate: True if all source segments are metadata
    - processing_order: {first_heading_index, first_source_line}
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import CHATT_API_KEY, CHATT_BASE_URL, CHATT_MODEL, PipelineConfig
from ..models import ClassificationResult, EECategory


# ---------------------------------------------------------------------------
# Metadata filtering patterns
# ---------------------------------------------------------------------------

# Strong metadata regex patterns (high-confidence skip candidates)
METADATA_PATTERNS = [
    r"^\d{1,3}$",  # Page number
    r"^v\d+\.\d+$",  # Version number
    r"^Rev\.\s*\d+\.\d+$",  # Revision
    r"^CONFIDENTIAL$",  # Confidential
    r"^DISTRIBUTION:",  # Distribution info
    r"^TABLE OF CONTENTS$",  # TOC
    r"^REVISION HISTORY$",  # Revision history
    r"^DEFINITIONS$",  # Definitions
    r"^ABBREVIATIONS$",  # Abbreviations
    r"^REFERENCES$",  # References
    r"^COPYRIGHT",  # Copyright
    r"^ALL RIGHTS RESERVED",  # Rights reserved
    r"^PROPERTY OF",  # Property notice
]

# Requirement signal keywords (any match = keep for M5)
REQUIREMENT_SIGNALS = [
    r"\bshall\b", r"\bmust\b", r"\brequired\b", r"\bneed to\b",
    r"\bcomply\b", r"\bverify\b", r"\btest\b", r"\bacceptance\b",
    r"\btolerance\b", r"\bvalidation\b", r"\bapproval\b",
    r"\b승인\b", r"\b요구\b", r"\b시험\b", r"\b검증\b",
    r"\b기준\b", r"\b허용\b", r"\b금지\b",
]


def _is_metadata_segment(segment: dict) -> tuple[bool, str]:
    """Check if a segment is likely metadata that can be safely skipped.

    Conservative criteria — only skip if ALL conditions are met:
    1. Very short text (< 50 chars)
    2. No requirement signals
    3. Matches strong metadata pattern

    Returns:
        (is_metadata, skip_reason)
    """
    content = segment.get("content", "").strip()

    # Condition 1: Very short text
    if len(content) < 50:
        # Condition 2: No requirement signals
        has_requirement_signal = any(
            re.search(sig, content, re.IGNORECASE)
            for sig in REQUIREMENT_SIGNALS
        )

        if not has_requirement_signal:
            # Condition 3: Match metadata pattern
            for pattern in METADATA_PATTERNS:
                if re.match(pattern, content, re.IGNORECASE):
                    return True, f"metadata_pattern: {pattern}"

    return False, ""


def _build_classification_payload(text: str, heading: str | None = None) -> dict:
    """Build the API request payload for classification."""
    full_text = text
    if heading:
        full_text = f"[Heading: {heading}]\n\n{text}"

    prompt = f"""You are an automotive E/E (Electrical/Electronic) requirements analyst.

Given the following text block from an automotive document, perform TWO classifications:

## Task 1: Is this a requirement statement?
A requirement statement typically contains:
- "shall", "must", "should", "required", "required to", "shall not", "must not"
- Or expresses a mandatory condition/specification

## Task 2: Is this related to E/E (Electrical/Electronic) components?
E/E components include: sensors, controllers (ECU/TCU/VCU/BMS/MCU), actuators,
electric motors, power electronics (inverters, converters), power supply/wiring,
communication networks (CAN, LIN, Ethernet), diagnostics, EMC, and related interfaces.

## Text to analyze:
{full_text}

## Output format (JSON only, no markdown):
{{
  "is_requirement": true/false,
  "is_ee_related": true/false,
  "ee_category": "Sensor / ECU & Software / Actuator & Motor / Power Electronics / Power & Wiring / Communication & Diagnostics / EMC & Electrical Environment / Other",
  "reasoning": "Brief explanation"
}}"""

    return {
        "model": CHATT_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise automotive E/E requirements classification assistant. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse the LLM JSON response, handling markdown code blocks and reasoning text."""
    if response_text is None:
        raise ValueError("LLM returned None content")
    text = response_text.strip()

    # Strip markdown code blocks if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    # Find the first complete JSON object by locating matching braces
    brace_start = text.find("{")
    if brace_start == -1:
        return {}

    brace_count = 0
    brace_end = -1
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            brace_count += 1
        elif text[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                brace_end = i + 1
                break

    if brace_end == -1:
        return {}

    json_str = text[brace_start:brace_end]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {}


async def _classify_single_async(
    text: str,
    heading: str | None = None,
    segment_id: str = "",
    batch_id: str = "",
    retry_count: int = 0,
) -> dict[str, Any]:
    """Call ChatT API for a single classification (async version)."""
    payload = _build_classification_payload(text, heading)

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
    return _parse_llm_response(content)


async def _classify_with_retry(
    text: str,
    heading: str | None = None,
    segment_id: str = "",
    batch_id: str = "",
    max_retries: int = 3,
) -> tuple[dict[str, Any], bool]:
    """Call ChatT API with retry logic and bounded concurrency.

    Returns:
        (result, success) — result is the parsed response, success indicates if it succeeded
    """
    for attempt in range(max_retries):
        try:
            result = await _classify_single_async(
                text, heading, segment_id, batch_id, attempt
            )
            return result, True
        except Exception as e:
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"       [M5] Retry {attempt+1}/{max_retries} for {segment_id} after {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            else:
                return {"error": str(e)}, False


async def classify_batch_parallel(
    batches: list[dict],
    max_concurrency: int = 4,
) -> list[ClassificationResult]:
    """Classify merged batches in parallel with bounded concurrency.

    Each batch from M4 contains:
      - merged_text: text to classify
      - source_segments: list of original segment info
      - batch_id: batch identifier
      - skip_candidate: True if all source segments are metadata

    Args:
        batches: List of batch dicts from M4.
        max_concurrency: Maximum number of parallel API calls (default: 4).
                         Increase to 8 after confirming stability.

    Returns:
        List of ClassificationResult objects (one per batch).
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[ClassificationResult] = []
    skipped_count = 0

    # Filter out skip_candidate batches first
    to_classify = []
    for i, batch in enumerate(batches):
        if batch.get("skip_candidate", False):
            skipped_count += 1
            print(f"[M5] Skipping skip_candidate batch {i+1}: {batch.get('skip_reason', '')}")
            continue
        to_classify.append(batch)

    print(f"[M5] {len(batches)} total batches, {skipped_count} skip_candidate skipped, {len(to_classify)} to classify")

    # All tasks share the semaphore — bounded concurrency handles everything
    async def classify_one(batch_item: dict, idx: int):
        async with semaphore:
            return await _classify_with_retry(
                batch_item.get("merged_text", ""),
                None,
                batch_item.get("batch_id", f"batch_{idx+1:03d}"),
                batch_item.get("batch_id", ""),
            )

    tasks = [classify_one(b, i) for i, b in enumerate(to_classify)]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for i, (result, success) in enumerate(batch_results):
        batch_item = to_classify[i]

        if isinstance(result, Exception) or not success:
            error_msg = str(result) if isinstance(result, Exception) else result.get("error", "Unknown error")
            print(f"       [M5] FAILED batch {batch_item.get('batch_id', '?')}: {error_msg}")
            results.append(ClassificationResult(
                block_id=batch_item.get("batch_id", f"batch_{i+1:03d}"),
                doc_id=batch_item.get("document_id", "unknown"),
                is_requirement=False,
                is_ee_related=False,
                reasoning=f"API error: {error_msg}",
                original_content=batch_item.get("merged_text", ""),
            ))
        else:
            if "error" in result:
                print(f"       [M5] ERROR: {result['error']}")
                continue

            # Parse ee_category
            ee_cat_str = result.get("ee_category", "Other")
            try:
                ee_category = EECategory(ee_cat_str)
            except ValueError:
                ee_category = EECategory.OTHER

            classification_result = ClassificationResult(
                block_id=batch_item.get("batch_id", f"batch_{i+1:03d}"),
                doc_id=batch_item.get("document_id", "unknown"),
                is_requirement=result.get("is_requirement", False),
                is_ee_related=result.get("is_ee_related", False),
                ee_category=ee_category,
                reasoning=result.get("reasoning", ""),
                original_content=batch_item.get("merged_text", ""),
            )
            results.append(classification_result)

            # Summary
            req_str = "REQ" if classification_result.is_requirement else "non-REQ"
            ee_str = "EE" if classification_result.is_ee_related else "non-EE"
            print(f"       → {req_str} / {ee_str} | {ee_category.value}")

    # Sort results by batch_id to maintain original order
    results.sort(key=lambda x: x.block_id)

    return results


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def classify_batch(segments: list[dict]) -> list[ClassificationResult]:
    """Classify a batch of segments via ChatT API (sequential, for compatibility).

    Args:
        segments: List of segment dicts with 'content' and optional 'heading'.

    Returns:
        List of ClassificationResult objects.
    """
    results: list[ClassificationResult] = []

    for i, seg in enumerate(segments):
        text = seg.get("content", "")
        heading = seg.get("heading")

        print(f"[M5] Classifying segment {i+1}/{len(segments)}...")

        try:
            llm_output = _classify_single(text, heading)

            if "error" in llm_output:
                print(f"       ERROR: {llm_output['error']}")
                continue

            # Parse ee_category
            ee_cat_str = llm_output.get("ee_category", "Other")
            try:
                ee_category = EECategory(ee_cat_str)
            except ValueError:
                ee_category = EECategory.OTHER

            result = ClassificationResult(
                block_id=f"seg_{i+1:03d}",
                doc_id=seg.get("doc_id", "unknown"),
                is_requirement=llm_output.get("is_requirement", False),
                is_ee_related=llm_output.get("is_ee_related", False),
                ee_category=ee_category,
                reasoning=llm_output.get("reasoning", ""),
                original_content=text,
            )
            results.append(result)

            # Summary
            req_str = "REQ" if result.is_requirement else "non-REQ"
            ee_str = "EE" if result.is_ee_related else "non-EE"
            print(f"       → {req_str} / {ee_str} | {ee_category.value}")

        except Exception as e:
            print(f"       FAILED: {e}")
            results.append(ClassificationResult(
                block_id=f"seg_{i+1:03d}",
                doc_id=seg.get("doc_id", "unknown"),
                is_requirement=False,
                is_ee_related=False,
                reasoning=f"API error: {e}",
                original_content=text,
            ))

    return results


def _classify_single(text: str, heading: str | None = None) -> dict[str, Any]:
    """Call ChatT API for a single classification (sync version)."""
    payload = _build_classification_payload(text, heading)

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
    return _parse_llm_response(content)


def run(config: PipelineConfig, batches: list[dict]) -> list[ClassificationResult]:
    """Execute M5: classify all merged batches via ChatT API.

    Uses parallel processing with bounded concurrency and metadata filtering.
    Writes classifications.jsonl to output.

    Args:
        batches: List of batch dicts from M4 (each with merged_text, source_segments, etc.)
    """
    import time as _time
    t0 = _time.time()
    results = classify_batch_parallel(batches, max_concurrency=4)
    m5_elapsed = _time.time() - t0

    classifications_path = config.run_dir / "classifications.jsonl"
    with open(classifications_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(r.model_dump_json() + "\n")

    # Statistics
    req_count = sum(1 for r in results if r.is_requirement)
    ee_count = sum(1 for r in results if r.is_ee_related)
    both_count = sum(1 for r in results if r.is_requirement and r.is_ee_related)

    print(f"[M5] {len(results)} classified | REQ: {req_count} | EE: {ee_count} | EE-REQ: {both_count}")
    print(f"[M5] Elapsed: {m5_elapsed:.2f}s ({m5_elapsed/len(results)*1000:.0f}ms/batch)")
    return results
