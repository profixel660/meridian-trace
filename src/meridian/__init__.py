"""Meridian — per-trade deliverables register from heterogeneous construction project documents.

See CONTEXT.md for the authoritative product brief and DATA_MODEL_V1.md for the schema.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("meridian")
except PackageNotFoundError:
    __version__ = "0.0.0+source"
