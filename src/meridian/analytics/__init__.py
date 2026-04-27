"""Analytics modules — pure SQL aggregations on the project DB.

Each submodule exposes a `compute(conn) -> *Result` and a
`write_xlsx(result, *, output_path)`. No LLM calls, no schema mutations.
"""
