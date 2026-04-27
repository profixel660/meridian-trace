"""Read versioned prompt files; extract the fenced ```...``` body block; render mustache-ish vars."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from meridian.config import settings

# Filenames are pinned to v1; future versions add new files alongside.
TEXT_SPEC_PROMPT_FILENAME = "PROMPT_V1_text_spec.md"
BOD_PROMPT_FILENAME = "PROMPT_V1_bod_import.md"
QUALITY_SCAN_PROMPT_FILENAME = "PROMPT_V1_quality_scan.md"

_FENCED_BLOCK = re.compile(r"```\n?(.*?)```", re.DOTALL)
_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_prompt(filename: str, *, prompts_dir: Path | None = None) -> str:
    """Read a prompt file in full (markdown wrapper included)."""
    base = prompts_dir or settings.prompts_path
    path = base / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def extract_prompt_body(markdown_text: str) -> str:
    """Pull the first fenced code block out of a versioned prompt file.

    Versioned prompt files wrap the operational prompt in a single ```...```
    block. Surrounding markdown is human-facing notes; only the fenced block
    is sent to the model.
    """
    match = _FENCED_BLOCK.search(markdown_text)
    if not match:
        raise ValueError("No fenced ```...``` body found in prompt file.")
    return match.group(1).strip()


def render_prompt(body: str, variables: dict[str, Any]) -> str:
    """Substitute `{{ name }}` placeholders. Missing keys render as empty string."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        return "" if value is None else str(value)

    return _VAR_PATTERN.sub(_replace, body)


__all__ = [
    "BOD_PROMPT_FILENAME",
    "QUALITY_SCAN_PROMPT_FILENAME",
    "TEXT_SPEC_PROMPT_FILENAME",
    "extract_prompt_body",
    "load_prompt",
    "render_prompt",
]
