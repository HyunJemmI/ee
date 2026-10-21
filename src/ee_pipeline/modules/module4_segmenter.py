"""M4: Requirement Segmenter — splits chunks into individual requirement candidates.

Breaks combined chunk content into individual text segments that can be
classified by the LLM. Each segment becomes a candidate for M5 classification.

Features:
- Context-based merging of short segments (<=500 chars) into ~1200-2000 char batches
- Conservative skip_candidate marking for obvious metadata only
- Full provenance tracking (heading_index, source_start_line, source_end_line)
- Same-document, adjacent-segment merging only
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import PipelineConfig
from ..models import DocumentBlock


# ---------------------------------------------------------------------------
# Metadata scoring system
# ---------------------------------------------------------------------------

# Scoring thresholds
SKIP_THRESHOLD = 5          # Score >= this -> skip_candidate
BLOCKING_THRESHOLD = 0      # Any blocking signal -> no skip regardless of score

# Blocking signals -- if ANY match, the segment MUST NOT be skipped
BLOCKING_SIGNALS = [
    r"\bshall\b", r"\bmust\b", r"\brequired\b", r"\bneed to\b",
    r"\bcomply\b", r"\bverify\b", r"\btest\b", r"\bacceptance\b",
    r"\btolerance\b", r"\bvalidation\b", r"\bapproval\b",
    r"\b\ub3c4\uad6c\b", r"\b\uc2dc\ud5d8\b", r"\b\uac80\uc99d\b",
    r"\b\uae30\uc900\b", r"\b\ud5c8\uc6a9\b", r"\b\uae40\uc9c0\b",
    r"\b\uacf5\ucc2c\b", r"\b\ud310\uc815\b", r"\b\uc218\uce58\b",
]

# Scoring patterns -- each adds points toward skip
# (+) = adds to metadata_score
# (-) = subtracts from metadata_score
_METADATA_SCORE_RULES = [
    # (+3) Page number only
    (r"^\d{1,3}$", 3, "page_number_only"),
    # (+2) Standard document header
    (r"^(TABLE OF CONTENTS|REVISION HISTORY|DEFINITIONS|ABBREVIATIONS|REFERENCES|CONTENTS)$", 2, "standard_header"),
    # (+2) Regulation/standard label
    (r"^(ISO|IEC|SAE|JIS|DIN|ANSI|ECE|UN)\s*\d+[\.\-]?\w*$", 2, "regulation_label"),
    # (+2) Repeated copyright/confidential
    (r"^(COPYRIGHT|CONFIDENTIAL|ALL RIGHTS RESERVED|PROPERTY OF|DISTRIBUTION:)", 2, "copyright_confidential"),
    # (+2) Version/revision marker
    (r"^(v\d+[\.\d]*|Rev\.\s*\d+[\.\d]*)$", 2, "version_marker"),
    # (+1) Boundary position (first/last segment of document)
    (r"^(Page|PAGE|p\.|p\. )\s*\d+", 1, "page_boundary"),
    # (+1) Very short text
    (r"^.{1,20}$", 1, "very_short"),
    # (-5) Requirement signal present
    (r"\bshall\b|\bmust\b|\brequired\b|\bneed to\b|\bcomply\b|\bverify\b|\btest\b|\bacceptance\b|\btolerance\b|\bvalidation\b|\bapproval\b|\b\ub3c4\uad6c\b|\b\uc2dc\ud5d8\b|\b\uac80\uc99d\b|\b\uae30\uc900\b|\b\ud5c8\uc6a9\b|\b\uae40\uc9c0\b|\b\uacf5\ucc2c\b|\b\ud310\uc815\b|\b\uc218\uce58\b", -5, "requirement_signal"),
    # (-5) Test/judgment/numeric criteria
    (r"(\uc2dc\ud5d8|\ud310\uc815|\ud5c8\uc6a9|\uacf5\ucc2c|\uae30\uc900|\uc218\uce58|\uac80\uc0ac|\ucc4c\uc815|\ud3b0\uac00|\u5224\u5b9a|\u8a66\u9a13|\u57fa\u6e96|\u8a31\u5bb9|\u516c\u5dee)", -5, "test_criteria"),
    # (-4) Table context
    (r"^\|.*\|$", -4, "table_row"),
    # (-3) Technical heading context
    (r"^(3\.\d+|4\.\d+|5\.\d+|6\.\d+|7\.\d+|8\.\d+|9\.\d+)\s+[A-Z]", -3, "technical_heading"),
]


# ---------------------------------------------------------------------------
# Merge configuration
# ---------------------------------------------------------------------------

MERGE_THRESHOLD = 500        # Short segment threshold (chars)
MERGE_TARGET_MIN = 1200      # Target merged size (chars)
MERGE_TARGET_MAX = 2000      # Max merged size (chars)

# Requirement signal keywords (any match = keep for M5) -- kept for backward compatibility
REQUIREMENT_SIGNALS = [
    r"\bshall\b", r"\bmust\b", r"\brequired\b", r"\bneed to\b",
    r"\bcomply\b", r"\bverify\b", r"\btest\b", r"\bacceptance\b",
    r"\btolerance\b", r"\bvalidation\b", r"\bapproval\b",
    r"\b\ub3c4\uad6c\b", r"\b\uc2dc\ud5d8\b", r"\b\uac80\uc99d\b",
    r"\b\uae30\uc900\b", r"\b\ud5c8\uc6a9\b", r"\b\uae40\uc9c0\b",
]


# ---------------------------------------------------------------------------
# Metadata detection helpers
# ---------------------------------------------------------------------------

def _has_blocking_signal(content: str) -> bool:
    """Check if content contains any blocking signal (must NOT skip)."""
    for sig in BLOCKING_SIGNALS:
        if re.search(sig, content, re.IGNORECASE):
            return True
    return False


def _compute_metadata_score(content: str) -> tuple[int, str]:
    """Compute a metadata score for a segment.

    Returns:
        (score, reason) -- score >= SKIP_THRESHOLD means skip_candidate
    """
    content = content.strip()
    if not content:
        return 10, "empty_content"  # Empty is always metadata

    # Blocking check -- if any blocking signal found, return immediately
    if _has_blocking_signal(content):
        return -100, "blocking_signal"

    score = 0
    reasons = []

    for pattern, points, label in _METADATA_SCORE_RULES:
        if re.search(pattern, content, re.IGNORECASE):
            score += points
            reasons.append(f"{label}(+{points})")

    reason = "; ".join(reasons) if reasons else "no_match"
    return score, reason


def _is_metadata_segment(segment: dict) -> tuple[bool, str | None]:
    """Determine if a segment is metadata (should be skipped).

    Uses blocking signals and metadata scoring to decide.
    Returns (is_metadata, skip_reason).
    """
    content = segment.get("content", "")
    if _has_blocking_signal(content):
        return False, None

    score, reason = _compute_metadata_score(content)
    if score >= SKIP_THRESHOLD:
        return True, reason
    return False, None


# ---------------------------------------------------------------------------
# Segment splitting
# ---------------------------------------------------------------------------

def _split_into_segments(content: str, heading: str | None = None,
                         chunk_provenance: dict | None = None) -> list[dict]:
    """Split chunk content into individual requirement candidates.

    Uses sentence-level splitting with context preservation.
    Each segment includes its heading context and provenance tracking.
    """
    if not content.strip():
        return []

    # Split by double newlines (paragraph boundaries)
    paragraphs = re.split(r"\n\s*\n+", content.strip())

    segments: list[dict] = []
    for i, para in enumerate(paragraphs):
        para = para.strip()
        if not para or len(para) < 10:
            continue

        # Determine line range for this segment
        start_line = None
        end_line = None
        if chunk_provenance and chunk_provenance.get("source_line_ranges"):
            line_ranges = chunk_provenance["source_line_ranges"]
            if i < len(line_ranges):
                start_line = line_ranges[i].get("start_line")
                end_line = line_ranges[i].get("end_line")

        segment = {
            "segment_id": f"seg_{i+1:03d}",
            "heading": heading,
            "content": para,
            "length": len(para),
            "skip_candidate": False,
            "skip_reason": None,
        }

        # Add provenance tracking
        if chunk_provenance:
            segment.update({
                "document_id": chunk_provenance.get("document_id", ""),
                "heading_index": chunk_provenance.get("heading_index"),
                "heading_text": chunk_provenance.get("heading_text"),
                "source_start_line": start_line,
                "source_end_line": end_line,
                "parent_heading": chunk_provenance.get("parent_heading"),
            })

        # Check for metadata
        is_metadata, skip_reason = _is_metadata_segment(segment)
        if is_metadata:
            segment["skip_candidate"] = True
            segment["skip_reason"] = skip_reason

        segments.append(segment)

    return segments


# ---------------------------------------------------------------------------
# Context-based merging
# ---------------------------------------------------------------------------

def _merge_short_segments(segments: list[dict]) -> list[dict]:
    """Merge short segments (<=500 chars) with adjacent segments for context.

    Rules:
    - Only merge within the same document_id
    - Only merge adjacent segments (by source_start_line order)
    - Do not cross document boundaries, page boundaries, or major topic shifts
    - skip_candidate segments are preserved separately (not merged into content)
    - Merged batch target: ~1200-2000 chars

    Returns:
        List of merged batch dicts with full provenance tracking.
    """
    if not segments:
        return []

    # Sort by document_id then source_start_line to ensure correct order
    segments.sort(key=lambda s: (
        s.get("document_id", ""),
        s.get("source_start_line", 0) or 0,
    ))

    batches: list[dict] = []
    current_batch: list[dict] = []
    current_doc_id: str | None = None
    current_heading: str | None = None

    for seg in segments:
        seg_doc_id = seg.get("document_id", "")
        seg_heading = seg.get("heading")
        seg_content = seg.get("content", "")
        seg_length = len(seg_content)
        is_skip = seg.get("skip_candidate", False)

        # Initialize current_doc_id from first segment
        if current_doc_id is None:
            current_doc_id = seg_doc_id
            current_heading = seg_heading

        # Check document boundary -- start new batch
        if seg_doc_id != current_doc_id:
            if current_batch:
                batches.append(_build_batch(current_batch, current_doc_id))
                current_batch = []
            current_doc_id = seg_doc_id
            current_heading = seg_heading

        # Check heading/topic boundary -- start new batch if major shift
        if (current_batch and
            current_heading is not None and
            seg_heading is not None and
            seg_heading != current_heading and
            len(current_batch) > 0):
            # Only split if the current batch already has content
            current_content_len = sum(
                s.get("length", 0) for s in current_batch if not s.get("skip_candidate")
            )
            if current_content_len >= MERGE_TARGET_MIN:
                batches.append(_build_batch(current_batch, current_doc_id))
                current_batch = []
                current_heading = seg_heading

        # Handle skip_candidate segments -- keep separate, don't merge into content
        if is_skip:
            if current_batch:
                batches.append(_build_batch(current_batch, current_doc_id))
                current_batch = []
            # Add skip segment as its own batch
            batches.append(_build_batch([seg], current_doc_id))
            continue

        # Merge logic for non-skip segments
        current_content_len = sum(
            s.get("length", 0) for s in current_batch if not s.get("skip_candidate")
        )

        if not current_batch:
            # First segment in batch
            current_batch.append(seg)
            current_heading = seg_heading
        elif seg_length <= MERGE_THRESHOLD and (current_content_len + seg_length) <= MERGE_TARGET_MAX:
            # Short segment + fits in target -> merge
            current_batch.append(seg)
        elif current_content_len >= MERGE_TARGET_MIN:
            # Current batch is large enough -> flush and start new
            batches.append(_build_batch(current_batch, current_doc_id))
            current_batch = [seg]
            current_heading = seg_heading
        else:
            # Current batch not big enough, but adding this segment would exceed target
            # Flush current batch and start new with this segment
            batches.append(_build_batch(current_batch, current_doc_id))
            current_batch = [seg]
            current_heading = seg_heading

    # Flush remaining
    if current_batch:
        batches.append(_build_batch(current_batch, current_doc_id))

    return batches


def _build_batch(batch_segments: list[dict], doc_id: str) -> dict:
    """Build a merged batch dict with full provenance tracking."""
    # Build merged text (skip_candidate segments excluded from merged text)
    merged_parts = []
    source_segments = []
    source_segment_ids = []
    source_heading_indices = []
    source_line_ranges = []

    for seg in batch_segments:
        seg_id = seg.get("segment_id", "")
        seg_heading = seg.get("heading", "")
        seg_content = seg.get("content", "")
        seg_start = seg.get("source_start_line")
        seg_end = seg.get("source_end_line")
        seg_heading_idx = seg.get("heading_index")
        is_skip = seg.get("skip_candidate", False)

        # Include in source tracking always
        source_segments.append({
            "segment_id": seg_id,
            "heading_index": seg_heading_idx,
            "heading_text": seg_heading,
            "source_start_line": seg_start,
            "source_end_line": seg_end,
            "segment_text": seg_content,
            "skip_candidate": is_skip,
            "skip_reason": seg.get("skip_reason"),
        })
        source_segment_ids.append(seg_id)
        if seg_heading_idx is not None:
            source_heading_indices.append(seg_heading_idx)
        if seg_start is not None:
            source_line_ranges.append({
                "start_line": seg_start,
                "end_line": seg_end,
            })

        # Include in merged text only if not skip_candidate
        if not is_skip:
            if seg_heading and not merged_parts:
                merged_parts.append(f"[Heading: {seg_heading}]")
            merged_parts.append(seg_content)

    merged_text = "\n\n".join(merged_parts)

    # Determine processing status
    all_skip = all(s.get("skip_candidate", False) for s in batch_segments)
    if all_skip:
        processing_status = "skip_candidate"
    else:
        processing_status = "processed"

    # Find first heading_index and source_line for ordering
    first_heading_idx = None
    first_source_line = None
    for sr in source_line_ranges:
        if sr.get("start_line") is not None:
            if first_source_line is None or sr["start_line"] < first_source_line:
                first_source_line = sr["start_line"]
    for hi in source_heading_indices:
        if first_heading_idx is None or hi < first_heading_idx:
            first_heading_idx = hi

    return {
        "document_id": doc_id,
        "batch_id": f"batch_{len(batch_segments):03d}",
        "processing_status": processing_status,
        "skip_candidate": all_skip,
        "skip_reason": batch_segments[0].get("skip_reason") if all_skip else None,
        "merged_text": merged_text,
        "source_segments": source_segments,
        "source_segment_ids": source_segment_ids,
        "source_heading_indices": source_heading_indices,
        "source_line_ranges": source_line_ranges,
        "processing_order": {
            "first_heading_index": first_heading_idx,
            "first_source_line": first_source_line,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def segment_chunks(chunks: list) -> list[dict]:
    """Segment all chunks into individual requirement candidates, then merge short ones.

    Args:
        chunks: List of ChunkedBlock objects or dicts with 'combined_content' and 'heading_hierarchy'.

    Returns:
        List of merged batch dicts ready for LLM classification with provenance tracking.
    """
    # Step 1: Split all chunks into segments
    all_segments: list[dict] = []

    for chunk in chunks:
        # Check if chunk is a dict (from JSONL) or object (from pipeline)
        if isinstance(chunk, dict):
            content = chunk.get("combined_content", "")
            heading = chunk.get("heading_hierarchy", "")
            # Extract document_id from blocks (dict format)
            doc_id = ""
            if chunk.get("blocks"):
                first_block = chunk["blocks"][0]
                if isinstance(first_block, dict):
                    doc_id = first_block.get("doc_id", "")
                else:
                    doc_id = getattr(first_block, "doc_id", "")
            chunk_provenance = {
                "document_id": doc_id,
                "heading_index": chunk.get("heading_index"),
                "heading_text": chunk.get("heading_text"),
                "source_line_ranges": chunk.get("source_line_ranges", []),
                "parent_heading": heading,
            }
        elif hasattr(chunk, 'combined_content'):
            content = chunk.combined_content
            heading = chunk.heading_hierarchy
            chunk_provenance = {
                "document_id": chunk.blocks[0].doc_id if chunk.blocks else "",
                "heading_index": chunk.source_heading_indices[0] if chunk.source_heading_indices else None,
                "heading_text": chunk.blocks[0].heading if chunk.blocks and chunk.blocks[0].heading else None,
                "source_line_ranges": chunk.source_line_ranges,
                "parent_heading": heading,
            }
        else:
            continue

        segments = _split_into_segments(content, heading, chunk_provenance)
        all_segments.extend(segments)

    # Step 2: Merge short segments
    batches = _merge_short_segments(all_segments)

    return batches


def run(config: PipelineConfig, chunks: list[dict]) -> list[dict]:
    """Execute M4: segment chunks, merge short ones, and output batches.

    Returns list of merged batch dicts with provenance tracking.
    Writes requirement_batches.jsonl to output.
    """
    batches = segment_chunks(chunks)

    # Statistics
    total_source_segments = sum(len(b["source_segments"]) for b in batches)
    skip_batches = sum(1 for b in batches if b["skip_candidate"])
    processed_batches = sum(1 for b in batches if not b["skip_candidate"])
    total_merged_chars = sum(len(b["merged_text"]) for b in batches)

    batches_path = config.run_dir / "requirement_batches.jsonl"
    with open(batches_path, "w", encoding="utf-8") as f:
        for batch in batches:
            f.write(json.dumps(batch, ensure_ascii=False) + "\n")

    print(f"[M4] {len(chunks)} chunks -> {total_source_segments} source segments -> {len(batches)} batches")
    print(f"     -> {processed_batches} to process | {skip_batches} skip_candidate")
    print(f"     -> avg merged size: {total_merged_chars // max(len(batches), 1)} chars")
    print(f"     -> {batches_path}")
    return batches