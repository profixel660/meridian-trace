"""Subprocess worker entry points (CONTEXT.md §6).

Each extraction job runs one subprocess per source for crash isolation. The
parent orchestrator spawns these via :mod:`meridian.workers.extraction_worker`.
"""
