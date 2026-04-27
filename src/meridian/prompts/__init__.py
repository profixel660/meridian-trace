"""Prompt loader — reads versioned prompt files from the repo's prompts/ directory."""

from meridian.prompts.loader import (
    BOD_PROMPT_FILENAME,
    QUALITY_SCAN_PROMPT_FILENAME,
    TEXT_SPEC_PROMPT_FILENAME,
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

__all__ = [
    "BOD_PROMPT_FILENAME",
    "QUALITY_SCAN_PROMPT_FILENAME",
    "TEXT_SPEC_PROMPT_FILENAME",
    "extract_prompt_body",
    "load_prompt",
    "render_prompt",
]
