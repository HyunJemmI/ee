"""M2.5: Keyword-based file pre-screening.

Filters out files that have no EE-related keywords, reducing LLM costs.
Uses the keyword lists from the attached HTML reference document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import PipelineConfig
from ..models import DocumentBlock, FileScreenResult, KeywordMatch


def _extract_keywords(text: str, keywords: dict) -> list[KeywordMatch]:
    """Find keyword matches in text using primary + secondary keyword lists."""
    matches: list[KeywordMatch] = []
    text_lower = text.lower()

    # Primary keywords (Section 3 of reference doc)
    for category, kw_list in keywords.get("primary", {}).items():
        for keyword in kw_list:
            count = len(re.findall(re.escape(keyword.lower()), text_lower))
            if count > 0:
                matches.append(KeywordMatch(
                    keyword=keyword,
                    category=category,
                    count=count,
                ))

    # Secondary keywords (Section 4 of reference doc)
    for category, kw_list in keywords.get("secondary", {}).items():
        for keyword in kw_list:
            count = len(re.findall(re.escape(keyword.lower()), text_lower))
            if count > 0:
                # Avoid duplicate if already matched in primary
                existing = next((m for m in matches if m.keyword == keyword), None)
                if existing is None:
                    matches.append(KeywordMatch(
                        keyword=keyword,
                        category=category,
                        count=count,
                    ))

    return matches


def screen_file(file_blocks: list[DocumentBlock], config: PipelineConfig) -> FileScreenResult:
    """Screen a single file's blocks for EE keywords.

    Decision logic:
    - If total keyword matches >= min_primary_keywords → PASS
    - Otherwise → SKIP
    """
    combined_text = "\n".join(b.content for b in file_blocks)
    matches = _extract_keywords(combined_text, config.keywords)
    total = sum(m.count for m in matches)

    decision = "PASS" if total >= config.min_primary_keywords else "SKIP"

    return FileScreenResult(
        doc_id=file_blocks[0].doc_id if file_blocks else "",
        source_file=file_blocks[0].source_file if file_blocks else "",
        matched_keywords=matches,
        total_matches=total,
        decision=decision,
    )


def run(config: PipelineConfig, blocks: list[DocumentBlock]) -> tuple[list[DocumentBlock], list[DocumentBlock]]:
    """Execute M2.5: screen files by keywords.

    Returns:
        (pass_blocks, skip_blocks) — files that pass and files that are skipped.
    """
    # Group blocks by doc_id
    doc_blocks: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        doc_blocks.setdefault(block.doc_id, []).append(block)

    pass_blocks: list[DocumentBlock] = []
    skip_blocks: list[DocumentBlock] = []
    prescreen_results: list[FileScreenResult] = []

    for doc_id, file_blocks in doc_blocks.items():
        result = screen_file(file_blocks, config)
        prescreen_results.append(result)

        if result.decision == "PASS":
            pass_blocks.extend(file_blocks)
            print(f"[M2.5] PASS  {doc_id} ({result.total_matches} keyword matches)")
        else:
            skip_blocks.extend(file_blocks)
            print(f"[M2.5] SKIP  {doc_id} (no EE keywords found)")

    # Write prescreen results
    results_path = config.run_dir / "prescreen_results.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in prescreen_results:
            f.write(r.model_dump_json() + "\n")

    # Write pass blocks
    pass_path = config.run_dir / "prescreen_pass_blocks.jsonl"
    with open(pass_path, "w", encoding="utf-8") as f:
        for b in pass_blocks:
            f.write(b.model_dump_json() + "\n")

    print(f"[M2.5] {len(pass_blocks)} blocks passed, {len(skip_blocks)} blocks skipped")
    return pass_blocks, skip_blocks