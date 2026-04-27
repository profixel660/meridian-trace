"""Source-document ingestion: hashing, dispatching by mime, extracting raw text + chunks."""

from meridian.ingest.dispatcher import IngestResult, ingest_file

__all__ = ["IngestResult", "ingest_file"]
