"""M4: split contextual chunks into requirement candidates and merge short context."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import ChunkedBlock

SKIP_THRESHOLD = 5
MERGE_THRESHOLD = 500
MERGE_TARGET_MIN = 1200
MERGE_TARGET_MAX = 2000


_SIGNAL_TERMS = (
    r"\bshall\b", r"\bmust\b", r"\brequired\b", r"\bneed to\b",
    r"\bcomply\b", r"\bverify\b", r"\btest\b", r"\bacceptance\b",
    r"\btolerance\b", r"\bvalidation\b", r"\bapproval\b",
    r"\bmaximum\b", r"\bminimum\b", r"\blimit\b", r"\bnot exceed\b",
    r"도구", r"시험", r"검증", r"기준", r"허용", r"공차", r"판정", r"수치",
)
_SIGNAL_PATTERN = re.compile("|".join(_SIGNAL_TERMS), re.IGNORECASE)
BLOCKING_SIGNALS = list(_SIGNAL_TERMS)
REQUIREMENT_SIGNALS = list(_SIGNAL_TERMS)
_BOILERPLATE_HEADING_PATTERN = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:legal information|legal disclaimer|disclaimer|contents|table of contents|"
    r"revision history|change history|document history|document feedback|suitability for use|limitation of liability|"
    r"warranty disclaimer|limited warranty|document status|product status|terms and conditions|export control)\s*[:.]?$",
    re.IGNORECASE,
)
_LEGAL_BOILERPLATE_PATTERN = re.compile(
    r"\b(?:copyright|all rights reserved|limitation of liability|warranty disclaimer|limited warranty|"
    r"suitability for use|terms and conditions|export control)\b",
    re.IGNORECASE,
)

_METADATA_SCORE_RULES: tuple[tuple[re.Pattern[str], int, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), points, label)
    for pattern, points, label in (
        (r"^\d{1,3}$", 3, "page_number_only"),
        (r"^(TABLE OF CONTENTS|REVISION HISTORY|DEFINITIONS|ABBREVIATIONS|REFERENCES|CONTENTS)$", 2, "standard_header"),
        (r"^(ISO|IEC|SAE|JIS|DIN|ANSI|ECE|UN)\s*\d+[.\-]?\w*$", 2, "regulation_label"),
        (r"^(COPYRIGHT|CONFIDENTIAL|ALL RIGHTS RESERVED|PROPERTY OF|DISTRIBUTION:)", 2, "copyright_confidential"),
        (r"^(v\d+[.\d]*|Rev\.\s*\d+[.\d]*)$", 2, "version_marker"),
        (r"^(Page|PAGE|p\.)\s*\d+", 1, "page_boundary"),
        (r"^.{1,20}$", 1, "very_short"),
        ("|".join(_SIGNAL_TERMS), -5, "requirement_signal"),
        (r"(시험|판정|허용|공차|기준|수치|검사|測定|評価|判定|試験|基準|許容|公差)", -5, "test_criteria"),
        (r"^\|.*\|$", -4, "table_row"),
        (r"^(?:3|4|5|6|7|8|9)\.\d+\s+[A-Z]", -3, "technical_heading"),
    )
)


def _has_blocking_signal(content: str) -> bool:
    """Return true when content contains a signal that must reach the LLM."""
    return bool(_SIGNAL_PATTERN.search(content))


def _compute_metadata_score(content: str) -> tuple[int, str]:
    """Score obvious document metadata; requirement-like text is protected."""
    text = content.strip()
    if not text:
        return 10, "empty_content"
    if _BOILERPLATE_HEADING_PATTERN.fullmatch(text) or _LEGAL_BOILERPLATE_PATTERN.search(text):
        return 10, "document_boilerplate"
    if _has_blocking_signal(text):
        return -100, "blocking_signal"

    score = 0
    reasons: list[str] = []
    for pattern, points, label in _METADATA_SCORE_RULES:
        if pattern.search(text):
            score += points
            reasons.append(f"{label}({points:+d})")
    return score, "; ".join(reasons) if reasons else "no_match"


def _is_metadata_segment(segment: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Return whether a segment should be skipped before classification."""
    score, reason = _compute_metadata_score(str(segment.get("content", "")))
    return (score >= SKIP_THRESHOLD, reason if score >= SKIP_THRESHOLD else None)


def _value(source: Any, key: str, default: Any = None) -> Any:
    return source.get(key, default) if isinstance(source, Mapping) else getattr(source, key, default)


def _split_into_segments(
    content: str,
    heading: str | None = None,
    chunk_provenance: Mapping[str, Any] | None = None,
    *,
    segment_prefix: str = "seg",
) -> list[dict[str, Any]]:
    """Split a chunk into paragraph candidates and attach provenance."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", content.strip()) if part.strip()]
    if not paragraphs:
        return []

    provenance = chunk_provenance or {}
    line_ranges = provenance.get("source_line_ranges", [])
    block_type = str(provenance.get("block_type", "paragraph"))
    section_role = str(provenance.get("section_role", "content"))
    candidate_type = "spec_table" if block_type == "table" else "paragraph"
    section_text = f"{heading or ''} {provenance.get('heading_text') or ''}"
    hard_metadata_context = bool(_BOILERPLATE_HEADING_PATTERN.fullmatch(section_text.strip()))
    if (
        block_type in {"metadata", "header", "footer"}
        or section_role in {"boilerplate", "support"}
        or hard_metadata_context
    ):
        candidate_type = "metadata"
    elif block_type == "figure":
        candidate_type = "figure"
    base_candidate_type = candidate_type
    segments: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        candidate_type = base_candidate_type
        source_range = line_ranges[min(index - 1, len(line_ranges) - 1)] if line_ranges else {}
        segment: dict[str, Any] = {
            "segment_id": f"{segment_prefix}_{index:05d}",
            "heading": heading,
            "content": paragraph,
            "length": len(paragraph),
            "skip_candidate": False,
            "skip_reason": None,
            "document_id": provenance.get("document_id", ""),
            "heading_index": provenance.get("heading_index"),
            "heading_text": provenance.get("heading_text") or heading,
            "source_file": provenance.get("source_file", ""),
            "source_start_line": source_range.get("start_line"),
            "source_end_line": source_range.get("end_line"),
            "source_block_id": provenance.get("source_block_id", ""),
            "page_number": provenance.get("page_number"),
            "source_bbox": provenance.get("source_bbox"),
            "parent_heading": provenance.get("parent_heading", heading or ""),
            "block_type": block_type,
            "section_role": section_role,
            "source_unit_id": provenance.get("source_unit_id"),
            "candidate_type": candidate_type,
            "table_id": provenance.get("table_id"),
            "table_title": provenance.get("table_title"),
            "table_headers": provenance.get("table_headers", []),
            "table_row_index": provenance.get("table_row_index"),
            "record_id": provenance.get("record_id"),
        }
        if (
            candidate_type not in {"figure", "metadata"}
            and section_role in {"metadata", "front_matter"}
            and not _has_blocking_signal(paragraph)
        ):
            segment["candidate_type"] = "metadata"
            candidate_type = "metadata"

        if candidate_type == "figure":
            segment["skip_candidate"] = True
            segment["skip_reason"] = "figure"
        elif candidate_type == "metadata":
            segment["skip_candidate"] = True
            segment["skip_reason"] = (
                "supporting_material"
                if section_role == "support"
                else "document_boilerplate"
                if section_role == "boilerplate"
                else "document_metadata"
            )
        elif (
            not re.search(r"[A-Za-z]{2,}", paragraph)
            and not _has_blocking_signal(paragraph)
            and candidate_type != "spec_table"
        ):
            segment["skip_candidate"] = True
            segment["skip_reason"] = "layout_fragment"
        is_metadata, reason = _is_metadata_segment(segment)
        if is_metadata:
            segment["skip_candidate"] = True
            segment["skip_reason"] = reason
        segments.append(segment)
    return segments


def _build_batch(batch_segments: list[dict[str, Any]], doc_id: str, batch_index: int | None = None) -> dict[str, Any]:
    """Create a serializable batch with complete source traceability."""
    source_segments: list[dict[str, Any]] = []
    merged_parts: list[str] = []
    heading_indices: list[int] = []
    line_ranges: list[dict[str, Any]] = []

    for segment in batch_segments:
        source_segments.append(
            {
                "segment_id": segment.get("segment_id", ""),
                "heading_index": segment.get("heading_index"),
                "heading_text": segment.get("heading_text") or segment.get("heading"),
                "source_file": segment.get("source_file", ""),
                "source_start_line": segment.get("source_start_line"),
                "source_end_line": segment.get("source_end_line"),
                "source_block_id": segment.get("source_block_id", ""),
                "page_number": segment.get("page_number"),
                "source_bbox": segment.get("source_bbox"),
                "segment_text": segment.get("content", ""),
                "skip_candidate": bool(segment.get("skip_candidate")),
                "skip_reason": segment.get("skip_reason"),
                "candidate_type": segment.get("candidate_type", "paragraph"),
                "block_type": segment.get("block_type", "paragraph"),
                "section_role": segment.get("section_role", "content"),
                "source_unit_id": segment.get("source_unit_id"),
                "table_id": segment.get("table_id"),
                "table_title": segment.get("table_title"),
                "table_headers": segment.get("table_headers", []),
                "table_row_index": segment.get("table_row_index"),
                "record_id": segment.get("record_id"),
            }
        )
        if segment.get("heading_index") is not None:
            heading_indices.append(segment["heading_index"])
        if segment.get("source_start_line") is not None:
            line_ranges.append(
                {
                    "start_line": segment.get("source_start_line"),
                    "end_line": segment.get("source_end_line"),
                }
            )
        if not segment.get("skip_candidate"):
            if segment.get("heading") and not merged_parts:
                merged_parts.append(f"[Heading: {segment['heading']}]")
            if segment.get("table_title") and not any(
                part.startswith("[Table: ") for part in merged_parts
            ):
                merged_parts.append(f"[Table: {segment['table_title']}]")
            merged_parts.append(segment.get("content", ""))

    source_ids = [segment["segment_id"] for segment in source_segments]
    all_skip = bool(source_segments) and all(segment["skip_candidate"] for segment in source_segments)
    first_source_line = min(
        (item["start_line"] for item in line_ranges if item.get("start_line") is not None),
        default=None,
    )
    first_heading_index = min(heading_indices, default=None)
    headings = list(
        dict.fromkeys(
            segment.get("heading_text") or segment.get("heading")
            for segment in batch_segments
            if segment.get("heading_text") or segment.get("heading")
        )
    )
    page_numbers = sorted(
        {
            int(segment["page_number"])
            for segment in batch_segments
            if segment.get("page_number") is not None
        }
    )
    table_ids = list(
        dict.fromkeys(segment.get("table_id") for segment in batch_segments if segment.get("table_id"))
    )
    table_titles = list(
        dict.fromkeys(segment.get("table_title") for segment in batch_segments if segment.get("table_title"))
    )
    record_ids = list(
        dict.fromkeys(segment.get("record_id") for segment in batch_segments if segment.get("record_id"))
    )
    source_unit_ids = list(
        dict.fromkeys(
            segment.get("source_unit_id")
            for segment in batch_segments
            if segment.get("source_unit_id")
        )
    )
    if batch_index is None:
        batch_id = f"batch_{source_ids[0]}" if source_ids else "batch_empty"
    else:
        batch_id = f"batch_{batch_index:04d}"

    return {
        "document_id": doc_id,
        "batch_id": batch_id,
        "processing_status": "skip_candidate" if all_skip else "processed",
        "skip_candidate": all_skip,
        "skip_reason": source_segments[0].get("skip_reason") if all_skip else None,
        "candidate_type": (
            source_segments[0].get("candidate_type", "paragraph")
            if source_segments
            else "paragraph"
        ),
        "heading": headings[0] if len(headings) == 1 else " > ".join(headings) if headings else None,
        "heading_index": first_heading_index,
        "page_numbers": page_numbers,
        "source_file": source_segments[0].get("source_file", "") if source_segments else "",
        "table_id": table_ids[0] if len(table_ids) == 1 else None,
        "table_title": table_titles[0] if len(table_titles) == 1 else None,
        "record_id": record_ids[0] if len(record_ids) == 1 else None,
        "source_unit_id": source_unit_ids[0] if len(source_unit_ids) == 1 else None,
        "merged_text": "\n\n".join(merged_parts),
        "source_segments": source_segments,
        "source_segment_ids": source_ids,
        "source_heading_indices": heading_indices,
        "source_line_ranges": line_ranges,
        "processing_order": {
            "first_heading_index": first_heading_index,
            "first_source_line": first_source_line,
        },
    }


def _merge_short_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent short segments without crossing document boundaries."""
    if not segments:
        return []

    ordered = sorted(
        (dict(segment) for segment in segments),
        key=lambda segment: (
            segment.get("document_id", ""),
            segment.get("page_number") or 0,
            segment.get("source_start_line") or 0,
            segment.get("segment_id", ""),
        ),
    )
    batches: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_doc_id: str | None = None
    current_heading: str | None = None
    current_type: str | None = None
    current_table_id: str | None = None
    current_record_id: str | None = None
    current_source_unit_id: str | None = None

    def flush() -> None:
        nonlocal current
        if current:
            batches.append(_build_batch(current, current_doc_id or ""))
            current = []

    for segment in ordered:
        doc_id = segment.get("document_id", "")
        heading = segment.get("heading")
        if current_doc_id is None:
            current_doc_id, current_heading = doc_id, heading
            current_type = segment.get("candidate_type")
            current_table_id = segment.get("table_id")
            current_record_id = segment.get("record_id")
            current_source_unit_id = segment.get("source_unit_id")
        elif doc_id != current_doc_id:
            flush()
            current_doc_id, current_heading = doc_id, heading
            current_type = segment.get("candidate_type")
            current_table_id = segment.get("table_id")
            current_record_id = segment.get("record_id")
            current_source_unit_id = segment.get("source_unit_id")

        if segment.get("skip_candidate"):
            flush()
            batches.append(_build_batch([segment], doc_id))
            current_heading = heading
            continue

        current_length = sum(len(item.get("content", "")) for item in current)
        candidate_type = segment.get("candidate_type")
        table_id = segment.get("table_id")
        record_id = segment.get("record_id")
        source_unit_id = segment.get("source_unit_id")
        topic_changed = bool(
            current
            and (
                heading != current_heading
                or (current_type and candidate_type and candidate_type != current_type)
                or (current_table_id and table_id and table_id != current_table_id)
                or (current_record_id and record_id and record_id != current_record_id)
                or (record_id and current and record_id != current_record_id)
                or (
                    (current_source_unit_id is not None or source_unit_id is not None)
                    and source_unit_id != current_source_unit_id
                )
            )
        )
        if current and (
            topic_changed
            or (
            (
                current_length >= MERGE_TARGET_MIN
                and len(segment.get("content", "")) > MERGE_THRESHOLD
            )
            or current_length + len(segment.get("content", "")) > MERGE_TARGET_MAX
            )
        ):
            flush()

        if not current:
            current_heading = heading
            current_type = candidate_type
            current_table_id = table_id
            current_record_id = record_id
            current_source_unit_id = source_unit_id
        current.append(segment)

    flush()
    return batches


def _chunk_segments(
    chunk: ChunkedBlock | Mapping[str, Any],
    chunk_index: int,
) -> list[dict[str, Any]]:
    """Create typed segments from each source block instead of a chunk-wide type."""
    parent_heading = str(_value(chunk, "heading_hierarchy", "") or "")
    raw_blocks = _value(chunk, "blocks", []) or []
    segments: list[dict[str, Any]] = []
    for block_index, block in enumerate(raw_blocks, start=1):
        block_type = str(_value(block, "block_type", "paragraph"))
        if block_type == "heading":
            continue
        content = str(_value(block, "content", "") or "").strip()
        if not content:
            continue
        heading = _value(block, "heading", None) or parent_heading or None
        provenance = {
            "document_id": _value(block, "doc_id", ""),
            "heading_index": _value(block, "heading_index", None),
            "heading_text": heading,
            "source_file": _value(block, "source_file", ""),
            "source_line_ranges": [
                {
                    "start_line": _value(block, "source_start_line", None),
                    "end_line": _value(block, "source_end_line", None),
                }
            ],
            "source_block_id": _value(block, "block_id", ""),
            "page_number": _value(block, "page_number", None),
            "source_bbox": _value(block, "source_bbox", None),
            "parent_heading": parent_heading,
            "block_type": block_type,
            "section_role": _value(block, "section_role", "content"),
            "source_unit_id": _value(block, "source_unit_id", None),
            "table_id": _value(block, "table_id", None),
            "table_title": _value(block, "table_title", None),
            "table_headers": _value(block, "table_headers", []) or [],
            "table_row_index": _value(block, "table_row_index", None),
            "record_id": _value(block, "record_id", None),
        }
        segments.extend(
            _split_into_segments(
                content,
                heading,
                provenance,
                segment_prefix=f"seg{chunk_index:04d}b{block_index:04d}",
            )
        )
    return segments


def segment_chunks(
    chunks: list[ChunkedBlock | Mapping[str, Any]],
    *,
    include_skipped: bool = False,
) -> list[dict[str, Any]]:
    """Segment chunks and return processable batches by default."""
    all_segments: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        all_segments.extend(_chunk_segments(chunk, chunk_index))

    batches = _merge_short_segments(all_segments)
    for index, batch in enumerate(batches, start=1):
        batch["batch_id"] = f"batch_{index:04d}"
    if include_skipped:
        return batches
    processable = [batch for batch in batches if not batch["skip_candidate"]]
    for index, batch in enumerate(processable, start=1):
        batch["batch_id"] = f"batch_{index:04d}"
    return processable


def run(config: PipelineConfig, chunks: list[ChunkedBlock | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Execute M4 and write ``requirement_batches.jsonl``."""
    config.prepare_run_dir()
    all_batches = segment_chunks(chunks, include_skipped=True)
    batches = [batch for batch in all_batches if not batch["skip_candidate"]]
    skipped_batches = [batch for batch in all_batches if batch["skip_candidate"]]
    for index, batch in enumerate(batches, start=1):
        batch["batch_id"] = f"batch_{index:04d}"
    total_segments = sum(len(batch["source_segments"]) for batch in all_batches)
    total_chars = sum(len(batch["merged_text"]) for batch in batches)
    batches_path = write_jsonl(config.run_dir / "requirement_batches.jsonl", batches)
    write_jsonl(config.run_dir / "skipped_candidates.jsonl", skipped_batches)
    print(f"[M4] {len(chunks)} chunks -> {total_segments} source segments -> {len(batches)} candidate batches")
    print(f"     -> {len(batches)} to process | {len(skipped_batches)} skip_candidate")
    print(f"     -> avg merged size: {total_chars // max(len(batches), 1)} chars")
    print(f"     -> {batches_path}")
    return batches
