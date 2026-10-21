"""Pydantic models for the E/E Requirements Pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Document & block models
# ---------------------------------------------------------------------------

class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    XLS = "xls"


class DocumentBlock(BaseModel):
    """A parsed block from a document (e.g., a heading + its content)."""
    doc_id: str
    block_id: str
    heading: Optional[str] = None
    heading_level: int = 0
    content: str
    page_number: Optional[int] = None
    source_file: str
    # Provenance tracking
    heading_index: Optional[int] = None
    source_start_line: Optional[int] = None
    source_end_line: Optional[int] = None


class ChunkedBlock(BaseModel):
    """A contextual chunk combining related blocks."""
    chunk_id: str
    blocks: list[DocumentBlock] = Field(default_factory=list)
    combined_content: str
    heading_hierarchy: str = ""
    # Provenance tracking
    source_block_ids: list[str] = Field(default_factory=list)
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
    decision: str = Field(description="PASS | SKIP")


# ---------------------------------------------------------------------------
# LLM Classification models
# ---------------------------------------------------------------------------

class EECategory(str, Enum):
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
    is_requirement: bool
    is_ee_related: bool
    ee_category: Optional[EECategory] = None
    reasoning: str = ""
    original_content: str = ""


class TranslationResult(BaseModel):
    """Result of translation."""
    block_id: str
    doc_id: str
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
    heading: Optional[str] = None
    english_text: str
    korean_translation: str
    is_requirement: bool
    is_ee_related: bool
    ee_category: Optional[str] = None
    matched_keywords: list[str] = Field(default_factory=list)
    source_file: str
    page_number: Optional[int] = None
    # Provenance tracking
    heading_index: Optional[int] = None
    source_start_line: Optional[int] = None
    source_end_line: Optional[int] = None


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