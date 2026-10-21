"""Pydantic models for the E/E Requirements Pipeline."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Document & block models
# ---------------------------------------------------------------------------

class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    XLS = "xls"


class DocumentBlock(BaseModel):
    """A parsed block from a document (e.g., a heading + its content)."""
    doc_id: str
    block_id: str
    heading: str | None = None
    heading_level: int = 0
    content: str
    page_number: int | None = None
    source_file: str
    block_type: Literal["heading", "paragraph", "table", "figure", "footnote", "header", "footer", "metadata"] = "paragraph"
    # Keep physical structure separate from the document section's semantic role.
    # A table inside a revision-history section is still structurally a table,
    # but its section role is metadata. This prevents one overloaded field from
    # destroying information needed by later routing stages.
    section_role: Literal[
        "content", "metadata", "front_matter", "boilerplate", "support"
    ] = "content"
    # Optional atomic source container. Slides and spreadsheet rows should not
    # be merged across this boundary even when their headings are identical.
    source_unit_id: str | None = None
    source_bbox: tuple[float, float, float, float] | None = None
    font_size: float | None = None
    table_id: str | None = None
    table_title: str | None = None
    table_headers: list[str] = Field(default_factory=list)
    table_row_index: int | None = None
    record_id: str | None = None
    # Provenance tracking
    heading_index: int | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None


class ChunkedBlock(BaseModel):
    """A contextual chunk combining related blocks."""
    chunk_id: str
    blocks: list[DocumentBlock] = Field(default_factory=list)
    combined_content: str
    heading_hierarchy: str = ""
    block_types: list[str] = Field(default_factory=list)
    # Provenance tracking
    source_block_ids: list[str] = Field(default_factory=list)
    source_unit_ids: list[str] = Field(default_factory=list)
    source_heading_indices: list[int] = Field(default_factory=list)
    source_line_ranges: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Keyword filtering models
# ---------------------------------------------------------------------------

class KeywordMatch(BaseModel):
    """A keyword match found in a document."""
    keyword: str
    category: str
    count: int = 1


class FileScreenResult(BaseModel):
    """Result of keyword-based file screening."""
    doc_id: str
    source_file: str
    matched_keywords: list[KeywordMatch] = Field(default_factory=list)
    total_matches: int = 0
    decision: Literal["PASS", "SKIP"] = Field(description="PASS | SKIP")


# ---------------------------------------------------------------------------
# LLM input models
# ---------------------------------------------------------------------------

class LLMInputRecord(BaseModel):
    """Prompt-ready M4.5 record with a stable customer requirement identifier."""
    batch_id: str
    parent_batch_id: str
    doc_id: str
    cur_id: str
    heading: str | None = None
    candidate_type: Literal["paragraph", "spec_table"]
    original_text: str
    llm_text: str
    source_file: str = ""
    page_numbers: list[int] = Field(default_factory=list)
    table_id: str | None = None
    table_title: str | None = None
    record_id: str | None = None
    source_unit_id: str | None = None
    source_segments: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM Classification models
# ---------------------------------------------------------------------------

class EECategory(StrEnum):
    SENSOR = "Sensor"
    ECU_SOFTWARE = "ECU & Software"
    ACTUATOR_MOTOR = "Actuator & Motor"
    POWER_ELECTRONICS = "Power Electronics"
    POWER_WIRING = "Power & Wiring"
    COMM_DIAGNOSTICS = "Communication & Diagnostics"
    EMC_ELECTRICAL = "EMC & Electrical Environment"
    OTHER = "Other"


class ClassificationResult(BaseModel):
    """Result of LLM-based 2-axis classification."""
    block_id: str
    doc_id: str
    cur_id: str | None = None
    is_requirement: bool
    is_ee_related: bool
    ee_category: EECategory | None = None
    reasoning: str = ""
    original_content: str = ""


class TranslationResult(BaseModel):
    """Result of translation."""
    block_id: str
    doc_id: str
    cur_id: str | None = None
    original_text: str
    translated_text: str
    status: str = "OK"  # OK | REVIEW_NEEDED


# ---------------------------------------------------------------------------
# Final output models
# ---------------------------------------------------------------------------

class SpecRecord(BaseModel):
    """A final EE requirement record for the output report."""
    doc_id: str
    block_id: str
    cur_id: str | None = None
    heading: str | None = None
    english_text: str
    korean_translation: str
    is_requirement: bool
    is_ee_related: bool
    ee_category: str | None = None
    notes: str = ""
    matched_keywords: list[str] = Field(default_factory=list)
    source_file: str
    page_number: int | None = None
    # Provenance tracking
    heading_index: int | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    table_id: str | None = None
    record_id: str | None = None


class ClassificationSummary(BaseModel):
    """Summary statistics for the classification_summary sheet."""
    total_files_processed: int = 0
    files_passed_keyword_filter: int = 0
    files_skipped_by_keyword_filter: int = 0
    total_blocks_analyzed: int = 0
    is_requirement_true: int = 0
    is_requirement_false: int = 0
    is_ee_related_true: int = 0
    is_ee_related_false: int = 0
    ee_requirements_count: int = 0
    general_requirements_count: int = 0
    translation_complete: int = 0
    translation_review_needed: int = 0
