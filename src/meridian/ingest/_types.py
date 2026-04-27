"""Shared dataclasses for ingester output.

All format-specific ingesters (pdf, xlsx, docx, email) return `_Extracted`
containing a list of `_Chunk`. Defining these once here keeps the dispatcher
contract byte-identical across formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Chunk:
    chunk_kind: str
    locator: dict[str, Any]
    text: str


@dataclass
class _Extracted:
    extraction_method: str
    text: str
    chunks: list[_Chunk]
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["_Chunk", "_Extracted"]
