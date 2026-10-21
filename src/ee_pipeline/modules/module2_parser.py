"""M2: extract heading-aware blocks from supported office documents."""

from __future__ import annotations

import re
from collections.abc import Callable
from math import ceil
from pathlib import Path
from typing import Any

import pymupdf as fitz
from docx import Document as DocxDocument
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import DocumentBlock


def _flush_block(
    blocks: list[DocumentBlock],
    *,
    doc_id: str,
    source_file: str,
    block_id: str,
    heading: str | None,
    heading_level: int,
    content: list[str],
    page_number: int | None,
    heading_index: int,
    start_line: int | None,
    block_type: str = "paragraph",
    section_role: str = "content",
    source_unit_id: str | None = None,
    source_bbox: tuple[float, float, float, float] | None = None,
    font_size: float | None = None,
) -> None:
    """Append a block when content exists, centralizing provenance creation."""
    text = "\n".join(line.strip() for line in content if line.strip()).strip()
    if not text:
        return
    blocks.append(
        DocumentBlock(
            doc_id=doc_id,
            block_id=block_id,
            heading=heading,
            heading_level=heading_level,
            content=text,
            page_number=page_number,
            source_file=source_file,
            block_type=block_type,
            section_role=section_role,
            source_unit_id=source_unit_id,
            source_bbox=source_bbox,
            font_size=font_size,
            heading_index=heading_index if heading else None,
            source_start_line=start_line,
            source_end_line=(start_line + len(content) - 1) if start_line is not None else None,
        )
    )


# More specific numbered headings must come before the generic numbered rule.
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), level)
    for pattern, level in (
        (r"^(?:chapter|section)\s+\d+(?:[.\d]*)?(?:\s+.*)?$", 1),
        (r"^\d+\.\d+\.\d+\s+\S.*$", 4),
        (r"^\d+\.\d+\s+\S.*$", 3),
        (r"^\d{1,3}(?:\.\d+)*\s+\S.*$", 2),
        (r"^[IVX]+(?:\.\d+)*\s+\S.*$", 2),
        (r"^\[[A-Z][A-Z0-9_-]*\d+\]\s+\S.*$", 4),
    )
)

_METADATA_SECTION_PATTERN = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:revision history|change history|document history|"
    r"legal information|contents|table of contents|references|disclaimer)\s*[:.]?$",
    re.IGNORECASE,
)


def _is_table_like_heading(text: str) -> bool:
    """Reject numeric/table fragments that happen to match a heading regex."""
    normalized = " ".join(text.split())
    if not normalized or not re.search(r"[A-Za-z]{2,}", normalized):
        return True
    tokens = normalized.split()
    numeric_tokens = sum(bool(re.fullmatch(r"[\d.<>%+\-–—/Ωμµ°]+", token)) for token in tokens)
    placeholder_tokens = sum(token.upper() in {"N/A", "LIM", "---", "—", "-"} for token in tokens)
    return numeric_tokens >= max(3, len(tokens) // 2) or placeholder_tokens >= 2


def _detect_heading(
    text: str,
    font_size: float | None = None,
    body_font_size: float | None = None,
    *,
    is_bold: bool = False,
) -> dict[str, int] | None:
    """Return a heading level for a likely heading line."""
    normalized = " ".join(text.split())
    if len(normalized) > 180 or _is_table_like_heading(normalized):
        return None
    for pattern, level in _HEADING_PATTERNS:
        visually_prominent = (
            body_font_size is None
            or font_size is None
            or font_size >= body_font_size * 1.08
            or is_bold
        )
        if pattern.fullmatch(normalized) and visually_prominent:
            return {"level": level}
    if (
        is_bold
        and len(normalized) <= 100
        and font_size is not None
        and (body_font_size is None or font_size >= body_font_size * 1.15)
    ):
        return {"level": 1}
    return None


def _is_structured_content_heading(text: str) -> bool:
    """Return true for headings that can safely start a new technical section."""
    normalized = " ".join(text.split())
    return any(pattern.fullmatch(normalized) for pattern, _ in _HEADING_PATTERNS)


def _normalize_margin_text(text: str) -> str:
    """Normalize repeated page furniture without depending on vendor names."""
    normalized = " ".join(text.split()).casefold()
    normalized = re.sub(r"\b\d+\s+of\s+\d+\b", "<page> of <pages>", normalized)
    normalized = re.sub(r"\bpage\s+\d+\b", "page <number>", normalized)
    return normalized


def _repeated_margin_texts(document: Any) -> tuple[set[str], set[str]]:
    """Find headers and footers repeated across pages by position and frequency."""
    top_pages: dict[str, set[int]] = {}
    bottom_pages: dict[str, set[int]] = {}
    for page_index, page in enumerate(document):
        page_height = float(page.rect.height)
        for block in page.get_text("dict", sort=True).get("blocks", []):
            if block.get("type") != 0:
                continue
            text = " ".join(
                "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                for line in block.get("lines", [])
            ).strip()
            if not text:
                continue
            bbox = tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
            key = _normalize_margin_text(text)
            if bbox[1] < page_height * 0.14:
                top_pages.setdefault(key, set()).add(page_index)
            if bbox[3] > page_height * 0.90:
                bottom_pages.setdefault(key, set()).add(page_index)

    threshold = max(2, ceil(len(document) * 0.25))
    repeated_top = {text for text, pages in top_pages.items() if len(pages) >= threshold}
    repeated_bottom = {text for text, pages in bottom_pages.items() if len(pages) >= threshold}
    return repeated_top, repeated_bottom


def _pdf_block_type(
    text: str,
    bbox: tuple[float, float, float, float],
    page_height: float,
    repeated_headers: set[str] | None = None,
    repeated_footers: set[str] | None = None,
) -> str:
    """Classify a visual PDF block before semantic chunking."""
    normalized = " ".join(text.split())
    top, bottom = bbox[1], bbox[3]
    lowered = normalized.lower()
    margin_key = _normalize_margin_text(normalized)
    if top < page_height * 0.14 and margin_key in (repeated_headers or set()):
        return "header"
    if bottom > page_height * 0.90 or margin_key in (repeated_footers or set()):
        return "footer"
    if re.match(r"^(table|figure)\s+\d+", lowered):
        return "table" if lowered.startswith("table") else "figure"
    if len(re.findall(r"\b(?:min|typ|max|unit|parameter|symbol|conditions)\b", lowered)) >= 2:
        return "table"
    return "paragraph"


def _overlap_ratio(
    bbox: tuple[float, float, float, float],
    region: tuple[float, float, float, float],
) -> float:
    """Return the fraction of ``bbox`` covered by ``region``."""
    left = max(bbox[0], region[0])
    top = max(bbox[1], region[1])
    right = min(bbox[2], region[2])
    bottom = min(bbox[3], region[3])
    if right <= left or bottom <= top:
        return 0.0
    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1.0)
    return ((right - left) * (bottom - top)) / area


def _cell_text(page: Any, cell: tuple[float, float, float, float] | None) -> str:
    """Extract readable text from one detected PDF table cell."""
    if cell is None:
        return ""
    rect = fitz.Rect(cell)
    if rect.width > 2 and rect.height > 2:
        rect.x0 += 0.8
        rect.y0 += 0.8
        rect.x1 -= 0.8
        rect.y1 -= 0.8
    return " ".join(page.get_text("text", clip=rect, sort=True).split())


def _table_number(title: str) -> str | None:
    match = re.search(r"\btable\s+(\d+)\b", title, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_table_records(
    page: Any,
    page_number: int,
    doc_id: str,
    visual_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract row-aware tables and metadata from detected PDF cell geometry."""
    if not hasattr(page, "find_tables"):
        return [], []

    finder = page.find_tables()
    table_records: list[dict[str, Any]] = []
    table_regions: list[dict[str, Any]] = []
    captions = [
        record
        for record in visual_records
        if re.match(r"^Table\s+\d+\b", " ".join(record["text"].split()), re.IGNORECASE)
    ]

    for table_index, table in enumerate(finder.tables, start=1):
        bbox = tuple(float(value) for value in table.bbox)
        preceding = [
            record
            for record in captions
            if record["bbox"][1] <= bbox[1] and bbox[1] - record["bbox"][3] <= 160
        ]
        caption = max(preceding, key=lambda record: record["bbox"][1], default=None)
        title = " ".join(caption["text"].split()) if caption else f"Table {table_index}"
        number = _table_number(title) if caption else None
        table_id = f"{doc_id}_table_{number or f'p{page_number}_{table_index}'}"

        rows = list(table.rows)
        headers = [_cell_text(page, cell) for cell in rows[0].cells] if rows else []
        normalized_headers = [header or f"Column {index + 1}" for index, header in enumerate(headers)]
        table_regions.append(
            {
                "bbox": bbox,
                "table_id": table_id,
                "table_title": title,
                "table_headers": normalized_headers,
                "caption_bbox": caption["bbox"] if caption else None,
            }
        )

        inherited = ["" for _ in normalized_headers]
        for row_index, row in enumerate(rows[1:], start=1):
            cells = [_cell_text(page, cell) for cell in row.cells]
            nonempty = [value for value in cells if value]
            if not nonempty:
                continue

            is_section_row = len(nonempty) == 1 and not re.search(r"\d", nonempty[0])
            if is_section_row:
                inherited = ["" for _ in normalized_headers]
                row_text = f"Section: {nonempty[0]}"
            else:
                expanded: list[str] = []
                for column_index, value in enumerate(cells):
                    if value:
                        if column_index < 2:
                            inherited[column_index] = value
                        expanded.append(value)
                    elif column_index < 2 and inherited[column_index]:
                        expanded.append(inherited[column_index])
                    else:
                        expanded.append("")
                row_text = " | ".join(
                    f"{normalized_headers[index]}: {value}"
                    for index, value in enumerate(expanded)
                    if value
                )

            table_records.append(
                {
                    "text": row_text,
                    "lines": [row_text],
                    "bbox": tuple(float(value) for value in row.bbox),
                    "font_size": None,
                    "block_type": "table",
                    "table_id": table_id,
                    "table_title": title,
                    "table_headers": normalized_headers,
                    "table_row_index": row_index,
                    "sort_priority": 1,
                }
            )
    return table_records, table_regions


def _figure_regions(
    page: Any,
    visual_records: list[dict[str, Any]],
    table_regions: list[dict[str, Any]],
) -> list[tuple[float, float, float, float]]:
    """Locate drawn figure regions by matching captions to drawing clusters."""
    if not hasattr(page, "cluster_drawings"):
        return []
    clusters = [tuple(float(value) for value in rect) for rect in page.cluster_drawings()]
    regions: list[tuple[float, float, float, float]] = []
    for caption in visual_records:
        if not re.match(r"^Figure\s+\d+\b", " ".join(caption["text"].split()), re.IGNORECASE):
            continue
        caption_bbox = caption["bbox"]
        center_x = (caption_bbox[0] + caption_bbox[2]) / 2
        center_y = (caption_bbox[1] + caption_bbox[3]) / 2
        matches = [
            cluster
            for cluster in clusters
            if (
                cluster[0] - 2 <= center_x <= cluster[2] + 2
                and cluster[1] - 2 <= center_y <= cluster[3] + 2
            )
            and not any(_overlap_ratio(cluster, table["bbox"]) > 0.8 for table in table_regions)
        ]
        if matches:
            regions.append(min(matches, key=lambda cluster: (cluster[2] - cluster[0]) * (cluster[3] - cluster[1])))
        else:
            regions.append(caption_bbox)
    return regions


def _parse_pdf(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract layout-aware visual blocks from a PDF."""
    blocks: list[DocumentBlock] = []
    heading_index = 0
    source_file = Path(path).name

    active_heading: str | None = None
    active_level = 0
    active_metadata_section = False
    content_started = False
    with fitz.open(path) as document:
        repeated_headers, repeated_footers = _repeated_margin_texts(document)
        for page_number, page in enumerate(document, start=1):
            (
                page_blocks,
                heading_index,
                active_heading,
                active_level,
                active_metadata_section,
                content_started,
            ) = _parse_pdf_page(
                page,
                page_number,
                doc_id,
                source_file,
                heading_index,
                len(blocks),
                active_heading,
                active_level,
                active_metadata_section,
                content_started,
                repeated_headers,
                repeated_footers,
            )
            blocks.extend(page_blocks)

    return blocks


def _parse_pdf_page(
    page: Any,
    page_number: int,
    doc_id: str,
    source_file: str,
    heading_index: int,
    block_offset: int,
    active_heading: str | None,
    active_level: int,
    active_metadata_section: bool,
    content_started: bool,
    repeated_headers: set[str] | None = None,
    repeated_footers: set[str] | None = None,
) -> tuple[list[DocumentBlock], int, str | None, int, bool, bool]:
    """Parse one PDF page into layout-aware blocks."""
    # Do not let a front-matter heading such as "Contents" suppress every
    # later page when a vendor PDF fragments its real section headings.
    # Continuation pages still receive direct metadata detection below.
    active_metadata_section = False
    blocks: list[DocumentBlock] = []
    visual_blocks = [block for block in page.get_text("dict", sort=True).get("blocks", []) if block.get("type") == 0]
    page_height = float(page.rect.height)
    body_sizes = [
        float(span.get("size", 0))
        for block in visual_blocks
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ]
    body_font_size = sorted(body_sizes)[len(body_sizes) // 2] if body_sizes else None

    visual_records: list[dict[str, Any]] = []
    for visual_block in visual_blocks:
        lines: list[str] = []
        sizes: list[float] = []
        bold_span_count = 0
        text_span_count = 0
        for line in visual_block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if line_text:
                lines.append(line_text)
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                sizes.append(float(span.get("size", 0)))
                text_span_count += 1
                font_name = str(span.get("font", "")).casefold()
                if "bold" in font_name or int(span.get("flags", 0)) & 16:
                    bold_span_count += 1
        text = "\n".join(lines).strip()
        if not text:
            continue
        bbox = tuple(float(value) for value in visual_block.get("bbox", (0, 0, 0, 0)))
        font_size = max(sizes) if sizes else None
        visual_records.append(
            {
                "text": text,
                "lines": lines,
                "bbox": bbox,
                "font_size": font_size,
                "is_bold": bool(text_span_count and bold_span_count / text_span_count >= 0.5),
                "sort_priority": 0,
            }
        )

    table_records, table_regions = _extract_table_records(page, page_number, doc_id, visual_records)
    figure_regions = _figure_regions(page, visual_records, table_regions)
    flow_records: list[dict[str, Any]] = list(table_records)

    for record in visual_records:
        bbox = record["bbox"]
        overlapping_table = next(
            (table for table in table_regions if _overlap_ratio(bbox, table["bbox"]) >= 0.35),
            None,
        )
        if overlapping_table:
            continue

        table_context = next(
            (
                table
                for table in table_regions
                if table.get("caption_bbox")
                and table["caption_bbox"][1] - 2 <= bbox[1] <= table["bbox"][1]
            ),
            None,
        )
        in_figure = any(_overlap_ratio(bbox, region) >= 0.35 for region in figure_regions)
        if table_context:
            record["block_type"] = "table"
            record["table_id"] = table_context["table_id"]
            record["table_title"] = table_context["table_title"]
            record["table_headers"] = table_context["table_headers"]
            record["table_row_index"] = 0
        elif in_figure:
            record["block_type"] = "figure"
        else:
            record["block_type"] = _pdf_block_type(
                record["text"],
                bbox,
                page_height,
                repeated_headers,
                repeated_footers,
            )
        flow_records.append(record)

    flow_records.sort(key=lambda record: (record["bbox"][1], record.get("sort_priority", 0), record["bbox"][0]))

    for visual_index, record in enumerate(flow_records):
        text = record["text"]
        lines = record["lines"]
        bbox = record["bbox"]
        font_size = record.get("font_size")
        block_type = record.get("block_type", "paragraph")
        normalized_text = " ".join(text.split())
        metadata_heading = bool(_METADATA_SECTION_PATTERN.search(normalized_text))
        heading_match = (
            _detect_heading(
                normalized_text,
                font_size,
                body_font_size,
                is_bold=bool(record.get("is_bold")),
            )
            if block_type == "paragraph"
            else None
        )
        if metadata_heading and heading_match is None and len(normalized_text) <= 100:
            heading_match = {"level": 1}
        source_line = page_number * 1000 + visual_index

        if heading_match:
            active_heading = normalized_text
            active_level = heading_match["level"]
            active_metadata_section = metadata_heading or (
                active_metadata_section
                and not _is_structured_content_heading(normalized_text)
            )
            if not active_metadata_section:
                content_started = True
            heading_index += 1
            blocks.append(
                DocumentBlock(
                    doc_id=doc_id,
                    block_id=f"{doc_id}_p{page_number}_b{block_offset + len(blocks) + 1}",
                    heading=active_heading,
                    heading_level=active_level,
                    content=active_heading,
                    page_number=page_number,
                    source_file=source_file,
                    block_type="heading",
                    section_role="boilerplate" if active_metadata_section else "content",
                    source_bbox=bbox,
                    font_size=font_size,
                    heading_index=heading_index,
                    source_start_line=source_line,
                    source_end_line=source_line,
                )
            )
            continue

        direct_metadata = bool(_METADATA_SECTION_PATTERN.search(normalized_text))
        if block_type in {"header", "footer"}:
            section_role = "content"
        elif active_metadata_section or direct_metadata:
            section_role = "boilerplate"
        elif not content_started:
            section_role = "front_matter"
        else:
            section_role = "content"

        blocks.append(
            DocumentBlock(
                doc_id=doc_id,
                block_id=f"{doc_id}_p{page_number}_b{block_offset + len(blocks) + 1}",
                heading=active_heading,
                heading_level=active_level,
                content=text,
                page_number=page_number,
                source_file=source_file,
                block_type=block_type,
                section_role=section_role,
                source_bbox=bbox,
                font_size=font_size,
                table_id=record.get("table_id"),
                table_title=record.get("table_title"),
                table_headers=record.get("table_headers", []),
                table_row_index=record.get("table_row_index"),
                heading_index=heading_index if active_heading else None,
                source_start_line=source_line,
                source_end_line=source_line + max(0, len(lines) - 1),
            )
        )
    return (
        blocks,
        heading_index,
        active_heading,
        active_level,
        active_metadata_section,
        content_started,
    )


def _docx_heading_level(style_name: str) -> int | None:
    """Map a Word heading style to a numeric level."""
    match = re.search(r"heading\s*([1-9])", style_name.lower())
    return int(match.group(1)) if match else None


def _record_id(value: str) -> str | None:
    """Return a reusable structured-record ID when a cell looks identifier-like."""
    normalized = value.strip()
    if re.fullmatch(r"[A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)+", normalized, re.IGNORECASE):
        return normalized
    return None


def _parse_docx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract DOCX paragraphs and tables in their original document order."""
    blocks: list[DocumentBlock] = []
    document = DocxDocument(path)
    heading_index = 0
    active_heading: str | None = None
    active_level = 0
    content: list[str] = []
    start_line: int | None = None
    source_file = Path(path).name
    sequence_index = 0
    table_index = 0

    def flush_paragraphs() -> None:
        nonlocal content, start_line
        _flush_block(
            blocks,
            doc_id=doc_id,
            source_file=source_file,
            block_id=f"{doc_id}_b{len(blocks) + 1}",
            heading=active_heading,
            heading_level=active_level,
            content=content,
            page_number=None,
            heading_index=heading_index,
            start_line=start_line,
        )
        content, start_line = [], None

    for item in document.iter_inner_content():
        sequence_index += 1
        if isinstance(item, DocxParagraph):
            text = item.text.strip()
            if not text:
                continue
            item_heading_level = _docx_heading_level(item.style.name if item.style else "")
            if item_heading_level is not None:
                flush_paragraphs()
                active_heading = text
                active_level = item_heading_level
                heading_index += 1
                continue
            start_line = sequence_index if start_line is None else start_line
            content.append(text)
            continue

        if not isinstance(item, DocxTable):
            continue

        flush_paragraphs()
        table_index += 1
        raw_rows = [
            [" ".join(cell.text.split()) for cell in row.cells]
            for row in item.rows
        ]
        if not raw_rows:
            continue
        headers = [value or f"Column {index + 1}" for index, value in enumerate(raw_rows[0])]
        table_id = f"{doc_id}_table_{table_index}"
        for row_index, values in enumerate(raw_rows[1:], start=1):
            if not any(values):
                continue
            content_text = " | ".join(
                f"{headers[index]}: {value}"
                for index, value in enumerate(values[: len(headers)])
                if value
            )
            first_value = values[0] if values else ""
            blocks.append(
                DocumentBlock(
                    doc_id=doc_id,
                    block_id=f"{doc_id}_table{table_index}_r{row_index}",
                    heading=active_heading,
                    heading_level=active_level,
                    content=content_text,
                    source_file=source_file,
                    block_type="table",
                    source_unit_id=f"{table_id}_row_{row_index}",
                    table_id=table_id,
                    table_title=active_heading or f"Table {table_index}",
                    table_headers=headers,
                    table_row_index=row_index,
                    record_id=_record_id(first_value),
                    heading_index=heading_index if active_heading else None,
                    source_start_line=sequence_index,
                    source_end_line=sequence_index,
                )
            )

    flush_paragraphs()
    return blocks


def _clean_pptx_text(value: str) -> str:
    """Normalize PowerPoint soft line breaks and whitespace."""
    return " ".join(value.replace("\v", " ").split())


def _iter_pptx_leaf_shapes(shapes: Any) -> list[Any]:
    """Flatten grouped shapes so nested text and tables are not lost."""
    leaves: list[Any] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            leaves.extend(_iter_pptx_leaf_shapes(shape.shapes))
        else:
            leaves.append(shape)
    return sorted(leaves, key=lambda shape: (int(shape.top), int(shape.left), int(shape.shape_id)))


def _pptx_shape_texts(shape: Any) -> list[str]:
    if not getattr(shape, "has_text_frame", False):
        return []
    return [
        text
        for paragraph in shape.text_frame.paragraphs
        if (text := _clean_pptx_text(paragraph.text))
    ]


def _pptx_repeated_furniture(presentation: Any) -> set[str]:
    """Find repeated top/bottom text boxes such as branding and copyright."""
    top_occurrences: dict[str, set[int]] = {}
    bottom_occurrences: dict[str, set[int]] = {}
    slide_height = int(presentation.slide_height)
    for slide_index, slide in enumerate(presentation.slides):
        for shape in _iter_pptx_leaf_shapes(slide.shapes):
            texts = _pptx_shape_texts(shape)
            if not texts:
                continue
            top = int(shape.top)
            bottom = top + int(shape.height)
            key = " ".join(texts).casefold()
            if top <= slide_height * 0.16:
                top_occurrences.setdefault(key, set()).add(slide_index)
            if bottom >= slide_height * 0.84:
                bottom_occurrences.setdefault(key, set()).add(slide_index)
    top_threshold = max(2, ceil(len(presentation.slides) * 0.25))
    return {
        text for text, slides in top_occurrences.items() if len(slides) >= top_threshold
    } | {
        text for text, slides in bottom_occurrences.items() if len(slides) >= 2
    }


def _parse_pptx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract slide text and table rows while preserving slide atomicity."""
    blocks: list[DocumentBlock] = []
    heading_index = 0
    source_file = Path(path).name
    presentation = Presentation(path)
    repeated_furniture = _pptx_repeated_furniture(presentation)

    for slide_number, slide in enumerate(presentation.slides, start=1):
        leaf_shapes = _iter_pptx_leaf_shapes(slide.shapes)
        text_shapes = [
            shape
            for shape in leaf_shapes
            if _pptx_shape_texts(shape)
            and " ".join(_pptx_shape_texts(shape)).casefold() not in repeated_furniture
        ]
        table_shapes = [shape for shape in leaf_shapes if getattr(shape, "has_table", False)]
        if not text_shapes and not table_shapes:
            continue

        title_shape = slide.shapes.title
        text_shape_ids = {int(shape.shape_id) for shape in text_shapes}
        if title_shape is not None and int(title_shape.shape_id) not in text_shape_ids:
            title_shape = None
        if title_shape is None and text_shapes:
            short_candidates = [
                shape
                for shape in text_shapes
                if len(" ".join(_pptx_shape_texts(shape))) <= 180
            ]
            title_shape = min(
                short_candidates or text_shapes,
                key=lambda shape: (int(shape.top), int(shape.left)),
            )

        title_texts = _pptx_shape_texts(title_shape) if title_shape is not None else []
        heading = " ".join(title_texts) if title_texts else f"Slide {slide_number}"
        title_shape_id = int(title_shape.shape_id) if title_shape is not None else None
        body_texts = [
            text
            for shape in text_shapes
            if int(shape.shape_id) != title_shape_id
            for text in _pptx_shape_texts(shape)
        ]

        heading_index += 1
        source_unit_id = f"{doc_id}_slide_{slide_number}"
        if body_texts:
            _flush_block(
                blocks,
                doc_id=doc_id,
                source_file=source_file,
                block_id=f"{doc_id}_s{slide_number}_b{len(blocks) + 1}",
                heading=heading,
                heading_level=1,
                content=body_texts,
                page_number=slide_number,
                heading_index=heading_index,
                start_line=slide_number * 1000,
                source_unit_id=source_unit_id,
            )

        table_rows_added = 0
        for table_index, shape in enumerate(table_shapes, start=1):
            rows = [
                [_clean_pptx_text(cell.text) for cell in row.cells]
                for row in shape.table.rows
            ]
            if not rows:
                continue
            headers = [value or f"Column {index + 1}" for index, value in enumerate(rows[0])]
            table_id = f"{doc_id}_slide{slide_number}_table{table_index}"
            data_rows = rows[1:] if len(rows) > 1 else rows
            for row_index, values in enumerate(data_rows, start=1):
                if not any(values):
                    continue
                row_text = " | ".join(
                    f"{headers[index]}: {value}"
                    for index, value in enumerate(values[: len(headers)])
                    if value
                )
                blocks.append(
                    DocumentBlock(
                        doc_id=doc_id,
                        block_id=f"{doc_id}_s{slide_number}_t{table_index}_r{row_index}",
                        heading=heading,
                        heading_level=1,
                        content=row_text,
                        page_number=slide_number,
                        source_file=source_file,
                        block_type="table",
                        source_unit_id=source_unit_id,
                        table_id=table_id,
                        table_title=heading,
                        table_headers=headers,
                        table_row_index=row_index,
                        record_id=_record_id(values[0] if values else ""),
                        heading_index=heading_index,
                        source_start_line=slide_number * 1000 + table_index * 100 + row_index,
                        source_end_line=slide_number * 1000 + table_index * 100 + row_index,
                    )
                )
                table_rows_added += 1

        if not body_texts and not table_rows_added and title_texts:
            _flush_block(
                blocks,
                doc_id=doc_id,
                source_file=source_file,
                block_id=f"{doc_id}_s{slide_number}_b{len(blocks) + 1}",
                heading=heading,
                heading_level=1,
                content=[heading],
                page_number=slide_number,
                heading_index=heading_index,
                start_line=slide_number * 1000,
                source_unit_id=source_unit_id,
            )

    return blocks


_SHEET_REQUIREMENT_SIGNAL = re.compile(r"\b(?:shall|must|required|need(?:s)?\s+to)\b", re.IGNORECASE)
_SUPPORT_HEADER_TERMS = re.compile(
    r"\b(?:verification|test\s*case|evidence|coverage|traceability|result|status)\b",
    re.IGNORECASE,
)


def _sheet_section_role(
    sheet_name: str,
    headers: list[str],
    data_rows: list[tuple[int, list[str]]],
) -> str:
    """Infer sheet role from row semantics, using names only as fallback evidence."""
    row_texts = [" ".join(values) for _, values in data_rows]
    requirement_rows = sum(bool(_SHEET_REQUIREMENT_SIGNAL.search(text)) for text in row_texts)
    header_text = " ".join(headers)
    support_terms = len(_SUPPORT_HEADER_TERMS.findall(header_text))
    normalized_headers = {" ".join(header.casefold().split()) for header in headers}
    has_requirement_body = bool(
        normalized_headers
        & {"requirement", "requirement text", "requirement statement", "description"}
    )
    enough_requirement_rows = requirement_rows >= max(2, ceil(max(len(data_rows), 1) * 0.2))
    if enough_requirement_rows or (has_requirement_body and requirement_rows):
        return "content"
    if support_terms >= 2:
        return "support"
    if re.search(
        r"\b(?:summary|overview|contents|verification\s+matrix|traceability)\b",
        sheet_name,
        re.IGNORECASE,
    ):
        return "support"
    return "content"


def _append_spreadsheet_rows(
    blocks: list[DocumentBlock],
    *,
    doc_id: str,
    source_file: str,
    sheet_index: int,
    sheet_name: str,
    rows: list[tuple[int, list[str]]],
) -> None:
    """Append row-level blocks shared by XLSX and legacy XLS parsers."""
    if not rows:
        return
    headers = [header or f"Column {index + 1}" for index, header in enumerate(rows[0][1])]
    data_rows = rows[1:]
    section_role = _sheet_section_role(sheet_name, headers, data_rows)
    table_id = f"{doc_id}_sheet_{sheet_index}"
    for item_index, (row_index, values) in enumerate(data_rows, start=1):
        padded = values + [""] * max(0, len(headers) - len(values))
        content = " | ".join(
            f"{headers[index]}: {value}"
            for index, value in enumerate(padded[: len(headers)])
            if value
        )
        if not content:
            continue
        blocks.append(
            DocumentBlock(
                doc_id=doc_id,
                block_id=f"{doc_id}_sheet{sheet_index}_r{row_index}",
                heading=sheet_name,
                heading_level=1,
                content=content,
                page_number=sheet_index,
                source_file=source_file,
                block_type="table",
                section_role=section_role,
                source_unit_id=f"{table_id}_row_{row_index}",
                table_id=table_id,
                table_title=sheet_name,
                table_headers=headers,
                table_row_index=item_index,
                record_id=_record_id(padded[0] if padded else ""),
                heading_index=sheet_index,
                source_start_line=row_index,
                source_end_line=row_index,
            )
        )


def _parse_xlsx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract XLSX rows as traceable table records."""
    blocks: list[DocumentBlock] = []
    source_file = Path(path).name
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        for sheet_index, sheet_name in enumerate(workbook.sheetnames, start=1):
            sheet = workbook[sheet_name]
            rows: list[tuple[int, list[str]]] = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                values = ["" if value is None else str(value).strip() for value in row]
                while values and not values[-1]:
                    values.pop()
                if any(values):
                    rows.append((row_index, values))
            _append_spreadsheet_rows(
                blocks,
                doc_id=doc_id,
                source_file=source_file,
                sheet_index=sheet_index,
                sheet_name=sheet_name,
                rows=rows,
            )
    finally:
        workbook.close()
    return blocks


def _parse_xls(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract legacy XLS rows through the same contract as XLSX."""
    import pandas as pd

    blocks: list[DocumentBlock] = []
    source_file = Path(path).name
    workbook = pd.ExcelFile(path, engine="xlrd")
    try:
        for sheet_index, sheet_name in enumerate(workbook.sheet_names, start=1):
            frame = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=object)
            rows: list[tuple[int, list[str]]] = []
            for row_index, values in enumerate(
                frame.fillna("").itertuples(index=False, name=None),
                start=1,
            ):
                cells = [str(value).strip() for value in values]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append((row_index, cells))
            _append_spreadsheet_rows(
                blocks,
                doc_id=doc_id,
                source_file=source_file,
                sheet_index=sheet_index,
                sheet_name=str(sheet_name),
                rows=rows,
            )
    finally:
        workbook.close()
    return blocks


PARSERS: dict[str, Callable[[str, str], list[DocumentBlock]]] = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "pptx": _parse_pptx,
    "xlsx": _parse_xlsx,
    "xls": _parse_xls,
}


def parse_document(path: str, doc_id: str, doc_type: str) -> list[DocumentBlock]:
    """Parse one manifest entry; unsupported types return no blocks."""
    parser = PARSERS.get(doc_type.lower())
    if parser is None:
        print(f"[M2] WARNING: Unsupported type '{doc_type}' for {path}")
        return []
    return parser(path, doc_id)


def run(config: PipelineConfig, manifest: list[dict[str, Any]]) -> list[DocumentBlock]:
    """Execute M2, isolating malformed files so the rest of the run can continue."""
    config.prepare_run_dir()
    all_blocks: list[DocumentBlock] = []
    for entry in manifest:
        path = entry["path"]
        doc_id = entry["doc_id"]
        doc_type = entry["doc_type"]
        print(f"[M2] Parsing {doc_id} ({doc_type})...")
        try:
            blocks = parse_document(path, doc_id, doc_type)
        except Exception as error:
            print(f"[M2] WARNING: failed to parse {path}: {error}")
            blocks = []
        all_blocks.extend(blocks)
        print(f"       → {len(blocks)} blocks extracted")

    blocks_path = write_jsonl(config.run_dir / "document_blocks.jsonl", all_blocks)
    print(f"[M2] Total: {len(all_blocks)} blocks → {blocks_path}")
    return all_blocks
