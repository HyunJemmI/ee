"""M3: Contextual Chunker — groups blocks by heading hierarchy.

Combines related blocks into contextual chunks based on heading structure.
Creates proper context windows for LLM processing.
"""

from __future__ import annotations

from pathlib import Path

from ..config import PipelineConfig
from ..models import ChunkedBlock, DocumentBlock


def _build_heading_hierarchy(blocks: list[DocumentBlock]) -> str:
    """Build a heading hierarchy string from blocks."""
    headings = [b.heading for b in blocks if b.heading]
    if not headings:
        return ""
    return " > ".join(headings)


def _group_by_heading(blocks: list[DocumentBlock]) -> list[list[DocumentBlock]]:
    """Group blocks by their heading hierarchy.

    Blocks with the same or parent heading are grouped together.
    """
    if not blocks:
        return []

    groups: list[list[DocumentBlock]] = []
    current_group: list[DocumentBlock] = []
    current_top_heading: str | None = None

    for block in blocks:
        if block.heading and block.heading_level <= 2:
            # New top-level or section-level heading → start new group
            if current_group:
                groups.append(current_group)
            current_group = [block]
            current_top_heading = block.heading
        else:
            current_group.append(block)

    if current_group:
        groups.append(current_group)

    return groups


def chunk_blocks(blocks: list[DocumentBlock], max_chunk_size: int = 4000) -> list[ChunkedBlock]:
    """Chunk blocks into contextually meaningful groups.

    Args:
        blocks: List of document blocks to chunk.
        max_chunk_size: Maximum characters per chunk.

    Returns:
        List of ChunkedBlock objects with provenance tracking.
    """
    groups = _group_by_heading(blocks)
    chunks: list[ChunkedBlock] = []
    chunk_id_counter = 0

    for group in groups:
        combined = "\n\n".join(b.content for b in group if b.content)
        hierarchy = _build_heading_hierarchy(group)

        # If combined content is too large, split by blocks
        if len(combined) > max_chunk_size:
            sub_blocks: list[DocumentBlock] = []
            sub_content: list[str] = []
            sub_hierarchy = hierarchy
            sub_block_ids: list[str] = []
            sub_heading_indices: list[int] = []
            sub_line_ranges: list[dict] = []

            for block in group:
                block_text = block.content
                if len("\n".join(sub_content)) + len(block_text) > max_chunk_size and sub_content:
                    # Flush current sub-chunk
                    chunk_id_counter += 1
                    chunks.append(ChunkedBlock(
                        chunk_id=f"chunk_{chunk_id_counter:04d}",
                        blocks=sub_blocks,
                        combined_content="\n".join(sub_content),
                        heading_hierarchy=sub_hierarchy,
                        source_block_ids=sub_block_ids,
                        source_heading_indices=sub_heading_indices,
                        source_line_ranges=sub_line_ranges,
                    ))
                    sub_blocks = [block]
                    sub_content = [block_text]
                    sub_block_ids = [block.block_id]
                    sub_heading_indices = [block.heading_index] if block.heading_index is not None else []
                    sub_line_ranges = [{"start_line": block.source_start_line, "end_line": block.source_end_line}] if block.source_start_line is not None else []
                    sub_hierarchy = _build_heading_hierarchy([block])
                else:
                    sub_blocks.append(block)
                    sub_content.append(block_text)
                    sub_block_ids.append(block.block_id)
                    if block.heading_index is not None:
                        sub_heading_indices.append(block.heading_index)
                    if block.source_start_line is not None:
                        sub_line_ranges.append({"start_line": block.source_start_line, "end_line": block.source_end_line})

            if sub_blocks:
                chunk_id_counter += 1
                chunks.append(ChunkedBlock(
                    chunk_id=f"chunk_{chunk_id_counter:04d}",
                    blocks=sub_blocks,
                    combined_content="\n".join(sub_content),
                    heading_hierarchy=sub_hierarchy,
                    source_block_ids=sub_block_ids,
                    source_heading_indices=sub_heading_indices,
                    source_line_ranges=sub_line_ranges,
                ))
        else:
            chunk_id_counter += 1
            chunks.append(ChunkedBlock(
                chunk_id=f"chunk_{chunk_id_counter:04d}",
                blocks=group,
                combined_content=combined,
                heading_hierarchy=hierarchy,
                source_block_ids=[b.block_id for b in group],
                source_heading_indices=[b.heading_index for b in group if b.heading_index is not None],
                source_line_ranges=[{"start_line": b.source_start_line, "end_line": b.source_end_line} for b in group if b.source_start_line is not None],
            ))

    return chunks


def run(config: PipelineConfig, blocks: list[DocumentBlock]) -> list[ChunkedBlock]:
    """Execute M3: chunk blocks into contextual groups.

    Returns list of ChunkedBlock objects.
    Writes structured_blocks.jsonl to output.
    """
    chunks = chunk_blocks(blocks)

    chunks_path = config.run_dir / "structured_blocks.jsonl"
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")

    print(f"[M3] {len(blocks)} blocks → {len(chunks)} chunks → {chunks_path}")
    return chunks