"""M2.5: deterministic keyword screening before LLM processing."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from ..config import PipelineConfig
from ..io_utils import read_jsonl_models, write_jsonl
from ..models import DocumentBlock, FileScreenResult, KeywordMatch


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a case-insensitive whole-term pattern.

    Boundaries prevent a keyword such as ``CAN`` from matching ``candy`` while
    still supporting phrases and punctuation used in technical documents.
    """
    escaped = re.escape(keyword.strip())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _extract_keywords(text: str, keywords: dict) -> list[KeywordMatch]:
    """Find each configured keyword once with its total occurrence count."""
    matches: list[KeywordMatch] = []
    seen: set[str] = set()
    for group_name in ("primary", "secondary"):
        for category, keyword_list in keywords.get(group_name, {}).items():
            for raw_keyword in keyword_list:
                keyword = str(raw_keyword).strip()
                normalized = keyword.casefold()
                if not keyword or normalized in seen:
                    continue
                count = len(_keyword_pattern(keyword).findall(text))
                if count:
                    matches.append(KeywordMatch(keyword=keyword, category=category, count=count))
                    seen.add(normalized)
    return matches


def screen_file(
    file_blocks: list[DocumentBlock],
    config: PipelineConfig,
    *,
    doc_id: str | None = None,
    source_file: str | None = None,
) -> FileScreenResult:
    """Screen one file and return its decision plus keyword evidence."""
    combined_text = "\n".join(block.content for block in file_blocks)
    matches = _extract_keywords(combined_text, config.keywords)
    total_matches = sum(match.count for match in matches)
    resolved_doc_id = doc_id or (file_blocks[0].doc_id if file_blocks else "")
    resolved_source = source_file or (file_blocks[0].source_file if file_blocks else "")
    decision = "PASS" if total_matches >= config.min_primary_keywords else "SKIP"
    return FileScreenResult(
        doc_id=resolved_doc_id,
        source_file=resolved_source,
        matched_keywords=matches,
        total_matches=total_matches,
        decision=decision,
    )


def load_results(run_dir: Path) -> list[FileScreenResult]:
    """Load persisted screening results for M8 or standalone workflows."""
    return read_jsonl_models(run_dir / "prescreen_results.jsonl", FileScreenResult)


def run(
    config: PipelineConfig,
    blocks: list[DocumentBlock],
    manifest: Iterable[dict] | None = None,
) -> tuple[list[DocumentBlock], list[DocumentBlock]]:
    """Screen documents, persist evidence, and return pass/skip blocks.

    ``manifest`` is optional for backwards compatibility. When supplied, empty
    or unparseable files are still represented in the screening report.
    """
    config.prepare_run_dir()
    grouped: defaultdict[str, list[DocumentBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.doc_id].append(block)

    manifest_entries = list(manifest or [])
    document_ids = [entry["doc_id"] for entry in manifest_entries] if manifest_entries else sorted(grouped)

    pass_blocks: list[DocumentBlock] = []
    skip_blocks: list[DocumentBlock] = []
    results: list[FileScreenResult] = []

    for doc_id in document_ids:
        file_blocks = grouped.get(doc_id, [])
        entry = next((item for item in manifest_entries if item["doc_id"] == doc_id), None)
        result = screen_file(
            file_blocks,
            config,
            doc_id=doc_id,
            source_file=(Path(entry["path"]).name if entry else None),
        )
        results.append(result)
        if result.decision == "PASS":
            pass_blocks.extend(file_blocks)
            print(f"[M2.5] PASS  {doc_id} ({result.total_matches} keyword matches)")
        else:
            skip_blocks.extend(file_blocks)
            print(f"[M2.5] SKIP  {doc_id} ({result.total_matches} keyword matches)")

    write_jsonl(config.run_dir / "prescreen_results.jsonl", results)
    write_jsonl(config.run_dir / "prescreen_pass_blocks.jsonl", pass_blocks)
    print(f"[M2.5] {len(pass_blocks)} blocks passed, {len(skip_blocks)} blocks skipped")
    return pass_blocks, skip_blocks
