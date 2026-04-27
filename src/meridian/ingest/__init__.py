"""Source-document ingestion: hashing, dispatching by mime, extracting raw text + chunks."""

from meridian.ingest.dispatcher import (
    SUPPORTED_EXTENSIONS_BY_KIND,
    IngestResult,
    WalkResult,
    WalkSkip,
    ingest_file,
    walk_directory,
)

__all__ = [
    "SUPPORTED_EXTENSIONS_BY_KIND",
    "IngestResult",
    "WalkResult",
    "WalkSkip",
    "ingest_file",
    "walk_directory",
]
