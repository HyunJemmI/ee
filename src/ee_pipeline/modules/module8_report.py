"""M8: XLSX Report Generator — generates the final output report.

Creates an Excel file matching the template structure from the reference xlsx:
- Sheet 1: 문서분류 (Document Classification)
- Sheet 2: 요구사항분류 (Requirements Classification)
- Sheet 3: 고객사 요구사항 분석 (Customer Requirements Analysis)
- Sheet 4: 검토 절차 (Review Procedure)
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

from ..config import PipelineConfig
from ..models import ClassificationResult, SpecRecord, TranslationResult


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

HEADER_FONT = Font(bold=True, size=11, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="002C5F", end_color="002C5F", fill_type="solid")
SUBHEADER_FILL = PatternFill(start_color="EAF1F8", end_color="EAF1F8", fill_type="solid")
EE_REQ_FILL = PatternFill(start_color="DFF0D8", end_color="DFF0D8", fill_type="solid")
REVIEW_FILL = PatternFill(start_color="FCF3CF", end_color="FCF3CF", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def _apply_header_style(ws, row: int, max_col: int):
    """Apply header styling to a row."""
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER


def _apply_data_border(ws, row: int, max_col: int):
    """Apply border to a data row."""
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.border = THIN_BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="top")


# ---------------------------------------------------------------------------
# Sheet 1: 문서분류 (Document Classification)
# ---------------------------------------------------------------------------

DOC_CLASSIFY_HEADERS = [
    "문서 ID", "문서명", "문서 유형", "전장품 관련 여부",
    "검출 키워드 수", "판정 결과", "판정 근거",
]


def _write_doc_classification(ws, manifest, prescreen_results):
    """Write document classification sheet."""
    # Title row
    ws.cell(row=1, column=1, value="제 01. 문서분류 결과 요약")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(DOC_CLASSIFY_HEADERS))

    ws.cell(row=2, column=1, value="1항 : 고객사 요구사항 문서 중 전장품 관련 문서 선별 (A~E항 대응)")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(DOC_CLASSIFY_HEADERS))

    # Headers
    for col, header in enumerate(DOC_CLASSIFY_HEADERS, start=1):
        ws.cell(row=4, column=col, value=header)
    _apply_header_style(ws, 4, len(DOC_CLASSIFY_HEADERS))

    # Build prescreen lookup
    prescreen_map = {r.doc_id: r for r in prescreen_results}

    row_num = 5
    for entry in manifest:
        doc_id = entry["doc_id"]
        ps = prescreen_map.get(doc_id)

        matched_kw = ps.matched_keywords if ps else []
        total_kw = ps.total_matches if ps else 0
        decision = ps.decision if ps else "UNKNOWN"

        ws.cell(row=row_num, column=1, value=doc_id)
        ws.cell(row=row_num, column=2, value=Path(entry["path"]).name)
        ws.cell(row=row_num, column=3, value=entry["doc_type"].upper())
        ws.cell(row=row_num, column=4, value="관련" if decision == "PASS" else "비관련")
        ws.cell(row=row_num, column=5, value=total_kw)
        ws.cell(row=row_num, column=6, value=decision)
        ws.cell(row=row_num, column=7, value=", ".join(m.keyword for m in matched_kw[:5]))

        _apply_data_border(ws, row_num, len(DOC_CLASSIFY_HEADERS))
        if decision == "SKIP":
            for col in range(1, len(DOC_CLASSIFY_HEADERS) + 1):
                ws.cell(row=row_num, column=col).fill = REVIEW_FILL

        row_num += 1

    # Auto-width
    for col in range(1, len(DOC_CLASSIFY_HEADERS) + 1):
        ws.column_dimensions[chr(64 + col)].width = 20


# ---------------------------------------------------------------------------
# Sheet 2: 요구사항분류 (Requirements Classification)
# ---------------------------------------------------------------------------

REQ_CLASSIFY_HEADERS = [
    "Block ID", "문서 ID", "원문 (English)", "요구사항 여부",
    "전장품 관련 여부", "전장품 카테고리", "판정 근거",
]


def _write_req_classification(ws, classifications):
    """Write requirements classification sheet."""
    # Title
    ws.cell(row=1, column=1, value="제 02. 요구사항분류 결과 요약")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(REQ_CLASSIFY_HEADERS))

    ws.cell(row=2, column=1, value="2항 : 01. 문서분류에서 확인된 전장품 관련 문서의 요구사항을 AI 판별을 통해 전장품 관련 요구사항으로 선별")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(REQ_CLASSIFY_HEADERS))

    # Headers
    for col, header in enumerate(REQ_CLASSIFY_HEADERS, start=1):
        ws.cell(row=4, column=col, value=header)
    _apply_header_style(ws, 4, len(REQ_CLASSIFY_HEADERS))

    row_num = 5
    for cls in classifications:
        ws.cell(row=row_num, column=1, value=cls.block_id)
        ws.cell(row=row_num, column=2, value=cls.doc_id)
        ws.cell(row=row_num, column=3, value=cls.original_content[:500])  # Truncate long text
        ws.cell(row=row_num, column=4, value="Yes" if cls.is_requirement else "No")
        ws.cell(row=row_num, column=5, value="Yes" if cls.is_ee_related else "No")
        ws.cell(row=row_num, column=6, value=cls.ee_category.value if cls.ee_category else "N/A")
        ws.cell(row=row_num, column=7, value=cls.reasoning[:200])

        _apply_data_border(ws, row_num, len(REQ_CLASSIFY_HEADERS))

        # Highlight EE requirements
        if cls.is_requirement and cls.is_ee_related:
            for col in range(1, len(REQ_CLASSIFY_HEADERS) + 1):
                ws.cell(row=row_num, column=col).fill = EE_REQ_FILL

        row_num += 1

    # Auto-width
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 60
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 24
    ws.column_dimensions["G"].width = 40


# ---------------------------------------------------------------------------
# Sheet 3: 고객사 요구사항 분석 (Customer Requirements Analysis)
# ---------------------------------------------------------------------------

CRA_HEADERS = [
    "Doc. ID", "CuR ID", "Description", "Translation",
    "Customer ASIL", "Customer req. type", "Sample delivery",
    "Assigned to", "Supplier analysis status", "Supplier analysis comment",
    "Customer req. status", "Customer req. comment", "Source link",
]


def _write_cra_sheet(ws, spec_records):
    """Write the main Customer Requirements Analysis sheet."""
    # Headers
    for col, header in enumerate(CRA_HEADERS, start=1):
        ws.cell(row=2, column=col, value=header)
    _apply_header_style(ws, 2, len(CRA_HEADERS))

    row_num = 3
    for rec in spec_records:
        ws.cell(row=row_num, column=1, value=rec.doc_id)
        ws.cell(row=row_num, column=2, value=rec.block_id)
        ws.cell(row=row_num, column=3, value=rec.english_text[:1000])
        ws.cell(row=row_num, column=4, value=rec.korean_translation)
        ws.cell(row=row_num, column=5, value="")  # ASIL - to be filled
        ws.cell(row=row_num, column=6, value="EE Requirement")
        ws.cell(row=row_num, column=7, value="")  # Sample delivery
        ws.cell(row=row_num, column=8, value="")  # Assigned to
        ws.cell(row=row_num, column=9, value="AI Extracted")
        ws.cell(row=row_num, column=10, value=rec.ee_category or "")
        ws.cell(row=row_num, column=11, value="")  # Status
        ws.cell(row=row_num, column=12, value="")  # Comment
        ws.cell(row=row_num, column=13, value=rec.source_file)

        _apply_data_border(ws, row_num, len(CRA_HEADERS))
        for col in range(1, len(CRA_HEADERS) + 1):
            ws.cell(row=row_num, column=col).fill = EE_REQ_FILL

        row_num += 1

    # Auto-width
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 60
    ws.column_dimensions["D"].width = 60
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 18
    ws.column_dimensions["G"].width = 12
    ws.column_dimensions["H"].width = 16
    ws.column_dimensions["I"].width = 20
    ws.column_dimensions["J"].width = 24
    ws.column_dimensions["K"].width = 16
    ws.column_dimensions["L"].width = 20
    ws.column_dimensions["M"].width = 16


# ---------------------------------------------------------------------------
# Sheet 4: 검토 절차 (Review Procedure)
# ---------------------------------------------------------------------------

def _write_review_procedure(ws):
    """Write the review procedure sheet (static content from template)."""
    ws.cell(row=1, column=1, value="< 요구사항 분석 및 검토 절차 >")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)

    procedures = [
        "1. AI 추출 결과에 대한 설계 담당자 검토 필수",
        "2. 각 요구사항의 전장품 관련성 판단 근거 확인",
        "3. 누락된 요구사항이 없는지 원문 대조 확인",
        "4. 번역 품질 검토 (기술 용어 정확성 확인)",
        "5. 최종 판정은 설계 담당자가 확인 후 'Engineer Confirmed'로 변경",
    ]

    for i, proc in enumerate(procedures, start=3):
        ws.cell(row=i, column=1, value=proc)


# ---------------------------------------------------------------------------
# Main report generation
# ---------------------------------------------------------------------------

def generate_report(
    config: PipelineConfig,
    manifest: list[dict],
    prescreen_results,
    classifications: list[ClassificationResult],
    spec_records: list[SpecRecord],
) -> Path:
    """Generate the final XLSX report.

    Returns path to the generated file.
    """
    wb = Workbook()

    # Sheet 1: Document Classification
    ws1 = wb.active
    ws1.title = "문서분류"
    _write_doc_classification(ws1, manifest, prescreen_results)

    # Sheet 2: Requirements Classification
    ws2 = wb.create_sheet("요구사항분류")
    _write_req_classification(ws2, classifications)

    # Sheet 3: Customer Requirements Analysis
    ws3 = wb.create_sheet("고객사 요구사항 분석")
    _write_cra_sheet(ws3, spec_records)

    # Sheet 4: Review Procedure
    ws4 = wb.create_sheet("검토 절차")
    _write_review_procedure(ws4)

    # Save with timestamp to preserve previous reports
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = config.run_dir / f"final_report_{timestamp}.xlsx"
        wb.save(report_path)

    print(f"[M8] Report generated → {report_path}")
    return report_path


def run(config: PipelineConfig, manifest, prescreen_results, classifications, spec_records):
    """Execute M8: generate final XLSX report."""
    return generate_report(config, manifest, prescreen_results, classifications, spec_records)