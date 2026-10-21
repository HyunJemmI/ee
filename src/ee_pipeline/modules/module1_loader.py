"""M1: discover supported input documents and persist a deterministic manifest."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import DocumentType

SUPPORTED_EXTENSIONS: dict[str, DocumentType] = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".xlsx": DocumentType.XLSX,
    ".xls": DocumentType.XLS,
}


def _unique_doc_ids(paths: list[Path]) -> dict[Path, str]:
    """Return stable IDs while avoiding collisions between equal file stems."""
    seen: Counter[str] = Counter()
    result: dict[Path, str] = {}
    for path in paths:
        base_id = path.stem or "document"
        seen[base_id] += 1
        result[path] = base_id if seen[base_id] == 1 else f"{base_id}__{seen[base_id]}"
    return result


def discover_files(input_dir: Path) -> list[dict[str, Any]]:
    """Recursively discover supported documents in deterministic order.

    The first file keeps its stem as ``doc_id`` for backwards compatibility;
    duplicate stems receive a numeric suffix instead of silently overwriting
    one another downstream.
    """
    input_dir = Path(input_dir).expanduser()
    if not input_dir.is_dir():
        return []

    paths = sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: path.as_posix().lower(),
    )
    doc_ids = _unique_doc_ids(paths)

    return [
        {
            "path": str(path.resolve()),
            "doc_id": doc_ids[path],
            "doc_type": SUPPORTED_EXTENSIONS[path.suffix.lower()].value,
        }
        for path in paths
    ]


def run(config: PipelineConfig) -> list[dict[str, Any]]:
    """Execute M1 and write ``manifest.jsonl`` to the current run directory."""
    config.prepare_run_dir()
    manifest = discover_files(config.input_dir)
    manifest_path = write_jsonl(config.run_dir / "manifest.jsonl", manifest)
    print(f"[M1] Discovered {len(manifest)} files → {manifest_path}")
    return manifest
