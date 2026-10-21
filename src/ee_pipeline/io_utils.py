"""Small, shared persistence helpers used by pipeline modules."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class AsyncRequestPacer:
    """Serialize request starts so API RPM limits are not exceeded by bursts."""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        self._interval = 60.0 / requests_per_minute
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until the next request slot is available."""
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._interval
        if delay:
            await asyncio.sleep(delay)


def ensure_run_dir(run_dir: Path) -> Path:
    """Create and return a pipeline run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_jsonl(path: Path, rows: Iterable[Any]) -> Path:
    """Write dictionaries or Pydantic models as UTF-8 JSON Lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            value = row.model_dump(mode="json") if isinstance(row, BaseModel) else row
            output.write(json.dumps(value, ensure_ascii=False) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, ignoring blank lines."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def read_jsonl_models(path: Path, model_type: type[ModelT]) -> list[ModelT]:
    """Read JSONL into a Pydantic model type."""
    return [model_type.model_validate(row) for row in read_jsonl(path)]
