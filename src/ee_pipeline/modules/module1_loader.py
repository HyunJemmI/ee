"""M1: Document Loader — discovers input files and writes manifest.jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import INPUT_DIR, PipelineConfig
from ..models import DocumentType

SUPPORTED_EXTENSIONS = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".xlsx": DocumentType.XLSX,
    ".xls": DocumentType.XLS,
}


def discover_files(input_dir: Path) -> list[dict[str, Any]]:
    """Scan input_dir recursively for supported document files.

    Returns a list of dicts with keys: path, doc_id, doc_type.
    """
    manifest: list[dict[str, Any]] = []
    if not input_dir.is_dir():
        return manifest

    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            manifest.append({
                "path": str(path),
                "doc_id": path.stem,
                "doc_type": SUPPORTED_EXTENSIONS[path.suffix.lower()].value,
            })
    return manifest


def run(config: PipelineConfig) -> list[dict[str, Any]]:
    """Execute M1: discover files and write manifest.jsonl.

    Returns the manifest list.
    """
    config.run_dir.mkdir(parents=True, exist_ok=True)
    manifest = discover_files(config.input_dir)

    manifest_path = config.run_dir / "manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for entry in manifest:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[M1] Discovered {len(manifest)} files → {manifest_path}")
    return manifest