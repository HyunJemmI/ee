"""M4: Requirement Segmenter — splits chunks into individual requirement candidates.

Breaks combined chunk content into individual text segments that can be
classified by the LLM. Each segment becomes a candidate for M5 classification.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import PipelineConfig
from ..models import DocumentBlock


def _split_into_segments(content: str, heading: str | None = None) -> list[dict]:
    """Split chunk content into individual requirement candidates.

    Uses sentence-level splitting with context preservation.
    Each segment includes its heading context.
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

        segments.append({
            "segment_id": f"seg_{i+1:03d}",
            "heading": heading,
            "content": para,
            "length": len(para),
        })

    return segments


def segment_chunks(chunks: list) -> list[dict]:
    """Segment all chunks into individual requirement candidates.

    Args:
        chunks: List of ChunkedBlock objects or dicts with 'combined_content' and 'heading_hierarchy'.

    Returns:
        List of segment dicts ready for LLM classification.
    """
    all_segments: list[dict] = []

    for chunk in chunks:
        # Handle both Pydantic models and plain dicts
        if hasattr(chunk, 'combined_content'):
            content = chunk.combined_content
            heading = chunk.heading_hierarchy
        else:
            content = chunk.get("combined_content", "")
            heading = chunk.get("heading_hierarchy", "")

        segments = _split_into_segments(content, heading)
        all_segments.extend(segments)

    return all_segments


def run(config: PipelineConfig, chunks: list[dict]) -> list[dict]:
    """Execute M4: segment chunks into requirement candidates.

    Returns list of segment dicts.
    Writes requirement_candidates.jsonl to output.
    """
    segments = segment_chunks(chunks)

    candidates_path = config.run_dir / "requirement_candidates.jsonl"
    with open(candidates_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(seg["content"] + "\n---HEADING---\n" + seg.get("heading", "") + "\n")

    print(f"[M4] {len(chunks)} chunks → {len(segments)} segments → {candidates_path}")
    return segments