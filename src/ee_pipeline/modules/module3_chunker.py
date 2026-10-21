"""M3: group parsed blocks into bounded, provenance-aware context chunks."""

from __future__ import annotations

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import ChunkedBlock, DocumentBlock


def _build_heading_hierarchy(blocks: list[DocumentBlock]) -> str:
    """Build a de-duplicated heading path for a chunk."""
    headings: list[str] = []
    for block in blocks:
        heading = block.heading
        if heading and heading not in headings:
            headings.append(heading)
    return " > ".join(headings)


def _group_by_heading(blocks: list[DocumentBlock]) -> list[list[DocumentBlock]]:
    """Group adjacent blocks without crossing semantic or atomic boundaries."""
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_doc_id: str | None = None
    current_heading: str | None = None
    current_source_unit_id: str | None = None

    for block in blocks:
        starts_group = (
            bool(current)
            and (
                block.doc_id != current_doc_id
                or (block.heading is not None and block.heading != current_heading)
                or (
                    (block.source_unit_id is not None or current_source_unit_id is not None)
                    and block.source_unit_id != current_source_unit_id
                )
            )
        )
        if starts_group:
            groups.append(current)
            current = []
        current.append(block)
        current_doc_id = block.doc_id
        current_source_unit_id = block.source_unit_id
        if block.heading:
            current_heading = block.heading

    if current:
        groups.append(current)
    return groups


def _make_chunk(index: int, blocks: list[DocumentBlock]) -> ChunkedBlock:
    """Create a chunk and its provenance arrays from a block sequence."""
    content_parts = [
        block.content
        for block in blocks
        if block.content and block.block_type != "heading"
    ]
    return ChunkedBlock(
        chunk_id=f"chunk_{index:04d}",
        blocks=blocks,
        combined_content="\n\n".join(content_parts),
        heading_hierarchy=_build_heading_hierarchy(blocks),
        block_types=sorted({block.block_type for block in blocks}),
        source_block_ids=[block.block_id for block in blocks],
        source_unit_ids=list(
            dict.fromkeys(block.source_unit_id for block in blocks if block.source_unit_id)
        ),
        source_heading_indices=[
            block.heading_index for block in blocks if block.heading_index is not None
        ],
        source_line_ranges=[
            {"start_line": block.source_start_line, "end_line": block.source_end_line}
            for block in blocks
            if block.source_start_line is not None
        ],
    )


def _split_group(group: list[DocumentBlock], max_chunk_size: int, start_index: int) -> list[ChunkedBlock]:
    """Split a heading group at block boundaries while respecting the size limit."""
    chunks: list[ChunkedBlock] = []
    current: list[DocumentBlock] = []
    current_size = 0

    for block in group:
        block_size = len(block.content)
        separator_size = 2 if current else 0
        would_exceed = current and current_size + separator_size + block_size > max_chunk_size
        if would_exceed:
            chunks.append(_make_chunk(start_index + len(chunks), current))
            current = []
            current_size = 0
        current.append(block)
        current_size += (2 if len(current) > 1 else 0) + block_size

    if current:
        chunks.append(_make_chunk(start_index + len(chunks), current))
    return chunks


def chunk_blocks(blocks: list[DocumentBlock], max_chunk_size: int = 4000) -> list[ChunkedBlock]:
    """Chunk adjacent blocks into contextual groups.

    A single oversized block is kept intact so its original block identity and
    provenance remain lossless; all multi-block chunks stay within the limit.
    """
    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size must be positive")

    usable_blocks = [block for block in blocks if block.block_type not in {"header", "footer"}]
    chunks: list[ChunkedBlock] = []
    for group in _group_by_heading(usable_blocks):
        chunks.extend(_split_group(group, max_chunk_size, len(chunks) + 1))
    return [chunk for chunk in chunks if chunk.combined_content.strip()]


def run(config: PipelineConfig, blocks: list[DocumentBlock]) -> list[ChunkedBlock]:
    """Execute M3 and write ``structured_blocks.jsonl``."""
    config.prepare_run_dir()
    chunks = chunk_blocks(blocks)
    chunks_path = write_jsonl(config.run_dir / "structured_blocks.jsonl", chunks)
    print(f"[M3] {len(blocks)} blocks → {len(chunks)} chunks → {chunks_path}")
    return chunks
