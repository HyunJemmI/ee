"""M2: Document Parser — extracts heading-structured blocks from documents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from docx import Document as DocxDocument
from pptx import Presentation

from ..config import PipelineConfig
from ..models import DocumentBlock

# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------

def _parse_pdf(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract heading-structured blocks from a PDF."""
    blocks: list[DocumentBlock] = []
    doc = fitz.open(path)
    heading_index = 0

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text()
        lines = text.split("\n")

        current_heading: str | None = None
        current_level: int = 0
        current_content: list[str] = []
        current_start_line: int = 0

        def _flush():
            nonlocal current_heading, current_level, current_content, current_start_line, heading_index
            if current_content:
                blocks.append(DocumentBlock(
                    doc_id=doc_id,
                    block_id=f"{doc_id}_p{page_num}_b{len(blocks)+1}",
                    heading=current_heading,
                    heading_level=current_level,
                    content="\n".join(current_content).strip(),
                    page_number=page_num,
                    source_file=Path(path).name,
                    heading_index=heading_index if current_heading else None,
                    source_start_line=current_start_line,
                    source_end_line=current_start_line + len(current_content) - 1,
                ))
            current_heading = None
            current_level = 0
            current_content = []
            current_start_line = 0

        for line_idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Detect headings by font size or pattern
            heading_match = _detect_heading(stripped)
            if heading_match:
                _flush()
                current_heading = stripped
                current_level = heading_match["level"]
                current_start_line = page_num * 1000 + line_idx  # Approximate line number
                heading_index += 1
                current_content = []
            else:
                if not current_content:
                    current_start_line = page_num * 1000 + line_idx
                current_content.append(stripped)

        _flush()

    doc.close()
    return blocks


_HEADING_PATTERNS = [
    (r"^(?:Chapter|CHAPTER)\s+\d+", 1),
    (r"^(?:Section|SECTION)\s+\d+[\.\d]*", 1),
    (r"^(?:\d{1,3}[\.\d]*)\s+[A-Z][^.]*$", 2),
    (r"^(?:[IVX]+[\.\d]*)\s+[A-Z][^.]*$", 2),
    (r"^(?:\d{1,3})\.\d+\s+[A-Z][^.]*$", 3),
    (r"^(?:\d{1,3})\.\d+\.\d+\s+[A-Z][^.]*$", 4),
]


def _detect_heading(text: str) -> dict | None:
    """Detect if a line is a heading and return its level."""
    for pattern, level in _HEADING_PATTERNS:
        if re.match(pattern, text):
            return {"level": level}
    return None


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------

def _parse_docx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract heading-structured blocks from a DOCX."""
    blocks: list[DocumentBlock] = []
    doc = DocxDocument(path)

    block_id_counter = 0
    heading_index = 0
    current_heading: str | None = None
    current_level: int = 0
    current_content: list[str] = []
    current_start_line: int = 0

    def _flush():
        nonlocal current_heading, current_level, current_content, current_start_line, block_id_counter, heading_index
        if current_content:
            block_id_counter += 1
            blocks.append(DocumentBlock(
                doc_id=doc_id,
                block_id=f"{doc_id}_b{block_id_counter}",
                heading=current_heading,
                heading_level=current_level,
                content="\n".join(current_content).strip(),
                source_file=Path(path).name,
                heading_index=heading_index if current_heading else None,
                source_start_line=current_start_line,
                source_end_line=current_start_line + len(current_content) - 1,
            ))
        current_heading = None
        current_level = 0
        current_content = []
        current_start_line = 0

    for para_idx, para in enumerate(doc.paragraphs):
        style_name = para.style.name.lower() if para.style else ""
        text = para.text.strip()

        if not text:
            continue

        # Detect heading style
        heading_level = _docx_heading_level(style_name)
        if heading_level is not None:
            _flush()
            current_heading = text
            current_level = heading_level
            current_start_line = para_idx
            heading_index += 1
            current_content = []
        else:
            if not current_content:
                current_start_line = para_idx
            current_content.append(text)

    _flush()
    return blocks


def _docx_heading_level(style_name: str) -> int | None:
    """Map DOCX style name to heading level (1-4) or None."""
    if "heading 1" in style_name:
        return 1
    elif "heading 2" in style_name:
        return 2
    elif "heading 3" in style_name:
        return 3
    elif "heading 4" in style_name:
        return 4
    return None


# ---------------------------------------------------------------------------
# PPTX parser
# ---------------------------------------------------------------------------

def _parse_pptx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract heading-structured blocks from a PPTX."""
    blocks: list[DocumentBlock] = []
    prs = Presentation(path)
    heading_index = 0

    for slide_num, slide in enumerate(prs.slides, start=1):
        current_heading: str | None = None
        current_level: int = 0
        current_content: list[str] = []
        current_start_line: int = 0

        def _flush_slide():
            nonlocal current_heading, current_level, current_content, current_start_line, heading_index
            if current_content:
                blocks.append(DocumentBlock(
                    doc_id=doc_id,
                    block_id=f"{doc_id}_s{slide_num}_b{len(blocks)+1}",
                    heading=current_heading,
                    heading_level=current_level,
                    content="\n".join(current_content).strip(),
                    page_number=slide_num,
                    source_file=Path(path).name,
                    heading_index=heading_index if current_heading else None,
                    source_start_line=current_start_line,
                    source_end_line=current_start_line + len(current_content) - 1,
                ))
            current_heading = None
            current_level = 0
            current_content = []
            current_start_line = 0

        for shape_idx, shape in enumerate(slide.shapes):
            if not shape.has_text_frame:
                continue
            for para_idx, para in enumerate(shape.text_frame.paragraphs):
                text = para.text.strip()
                if not text:
                    continue

                # Simple heuristic: first text in slide = title/heading
                if current_heading is None:
                    _flush_slide()
                    current_heading = text
                    current_level = 1
                    current_start_line = slide_num * 1000 + shape_idx * 100 + para_idx
                    heading_index += 1
                    current_content = []
                else:
                    if not current_content:
                        current_start_line = slide_num * 1000 + shape_idx * 100 + para_idx
                    current_content.append(text)

        _flush_slide()

    return blocks


# ---------------------------------------------------------------------------
# XLSX/XLS parser
# ---------------------------------------------------------------------------

def _parse_xlsx(path: str, doc_id: str) -> list[DocumentBlock]:
    """Extract heading-structured blocks from an XLSX file.

    Each sheet becomes a block with the sheet name as heading.
    Cell content is joined row by row.
    """
    from openpyxl import load_workbook

    blocks: list[DocumentBlock] = []
    wb = load_workbook(path, data_only=True)

    for sheet_idx, sheet_name in enumerate(wb.sheetnames, start=1):
        sheet = wb[sheet_name]
        rows_data: list[str] = []
        for row_idx in range(1, sheet.max_row + 1):
            row_values = []
            for col_idx in range(1, sheet.max_column + 1):
                val = sheet.cell(row=row_idx, column=col_idx).value
                row_values.append(str(val) if val is not None else "")
            rows_data.append("\t".join(row_values))

        content = "\n".join(rows_data)
        blocks.append(DocumentBlock(
            doc_id=doc_id,
            block_id=f"{doc_id}_sheet{sheet_idx}_b1",
            heading=sheet_name,
            heading_level=1,
            content=content,
            source_file=Path(path).name,
        ))

    wb.close()
    return blocks


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

PARSERS = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "pptx": _parse_pptx,
    "xlsx": _parse_xlsx,
    "xls": _parse_xlsx,
}


def parse_document(path: str, doc_id: str, doc_type: str) -> list[DocumentBlock]:
    """Parse a single document and return its blocks."""
    parser = PARSERS.get(doc_type)
    if parser is None:
        print(f"[M2] WARNING: Unsupported type '{doc_type}' for {path}")
        return []
    return parser(path, doc_id)


def run(config: PipelineConfig, manifest: list[dict[str, Any]]) -> list[DocumentBlock]:
    """Execute M2: parse all documents from manifest.

    Returns list of all DocumentBlock objects.
    Writes document_blocks.jsonl to output.
    """
    all_blocks: list[DocumentBlock] = []

    for entry in manifest:
        path = entry["path"]
        doc_id = entry["doc_id"]
        doc_type = entry["doc_type"]

        print(f"[M2] Parsing {doc_id} ({doc_type})...")
        blocks = parse_document(path, doc_id, doc_type)
        all_blocks.extend(blocks)
        print(f"       → {len(blocks)} blocks extracted")

    # Write to JSONL
    blocks_path = config.run_dir / "document_blocks.jsonl"
    with open(blocks_path, "w", encoding="utf-8") as f:
        for block in all_blocks:
            f.write(block.model_dump_json() + "\n")

    print(f"[M2] Total: {len(all_blocks)} blocks → {blocks_path}")
    return all_blocks