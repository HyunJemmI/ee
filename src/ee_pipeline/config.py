"""Configuration for the E/E Requirements Pipeline."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# ChatT API configuration
# ---------------------------------------------------------------------------

CHATT_API_KEY: str = os.getenv("CHATT_API_KEY", "")
CHATT_BASE_URL: str = os.getenv("CHATT_BASE_URL", "https://chat.hyundai-transys.com/")
CHATT_MODEL: str = os.getenv("CHATT_MODEL", "ChatT-VIBE")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
KEYWORDS_DIR = DATA_DIR / "keywords"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

KEYWORDS_FILE = KEYWORDS_DIR / "ee_keywords.json"


# ---------------------------------------------------------------------------
# Keyword loading
# ---------------------------------------------------------------------------

def load_keywords() -> dict:
    """Load EE keywords from JSON file."""
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

CLASSIFICATION_PROMPT = """You are an automotive E/E (Electrical/Electronic) requirements analyst.

Given the following text block from an automotive document, perform TWO classifications:

## Task 1: Is this a requirement statement?
A requirement statement typically contains:
- "shall", "must", "should", "required", "required to", "shall not", "must not"
- Or expresses a mandatory condition/specification

## Task 2: Is this related to E/E (Electrical/Electronic) components?
E/E components include: sensors, controllers (ECU/TCU/VCU/BMS/MCU), actuators,
electric motors, power electronics (inverters, converters), power supply/wiring,
communication networks (CAN, LIN, Ethernet), diagnostics, EMC, and related interfaces.

## Text to analyze:
{text}

## Output format (JSON only):
{{
  "is_requirement": true/false,
  "is_ee_related": true/false,
  "ee_category": "Sensor / ECU & Software / Actuator & Motor / Power Electronics / Power & Wiring / Communication & Diagnostics / EMC & Electrical Environment / Other",
  "reasoning": "Brief explanation of your classification"
}}
"""

TRANSLATION_PROMPT = """Translate the following automotive E/E requirement statement from English to Korean.
Maintain technical accuracy and use standard automotive industry terminology.

Original: {text}

Provide only the Korean translation, nothing else.
"""


# ---------------------------------------------------------------------------
# Dataclass for pipeline config
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    input_dir: Path = field(default_factory=lambda: INPUT_DIR)
    output_dir: Path = field(default_factory=lambda: OUTPUT_DIR)
    _run_dir: Path | None = field(default=None, repr=False)
    chatt_api_key: str = CHATT_API_KEY
    chatt_base_url: str = CHATT_BASE_URL
    chatt_model: str = CHATT_MODEL
    keywords: dict = field(default_factory=load_keywords)
    # Minimum keywords to consider a file as EE-related
    min_primary_keywords: int = 1
    # Confidence threshold for AI prescreening
    ai_confidence_threshold: float = 0.7

    @property
    def run_dir(self) -> Path:
        """Per-run output subfolder: output/run_YYYYMMDD_HHMMSS/"""
        if self._run_dir is None:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._run_dir = self.output_dir / f"run_{ts}"
        return self._run_dir