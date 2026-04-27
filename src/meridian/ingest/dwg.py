"""DWG ingest via ODA File Converter -> PDF -> PDF text extraction.

ODA File Converter is an external native tool (free for non-commercial use)
that converts AutoCAD `.dwg` files to other formats including PDF. We shell
out to it, then route the produced PDF through the existing PDF ingester.

The conversion chain (DWG -> PDF -> PDF text) is lossy: text labels and
annotations typically survive (so the PDF ingester can pick them up via
pdfplumber or OCR fallback), but vector geometry does not.

The binary is not installable via pip; the user installs it separately.
See `_dwg_setup.md` for install instructions.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from meridian.ingest import pdf as pdf_ingest
from meridian.ingest._types import _Extracted

# Env var the user may set to point at the ODA File Converter executable
# directly (bypasses PATH lookup and the Windows-default install-path glob).
_ODA_ENV_VAR = "ODA_FILE_CONVERTER"

# Public install URL surfaced in the FileNotFoundError message so the CLI
# can show a helpful skip message.
_ODA_INSTALL_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

# Glob for the default Windows install layout. ODA installs each version
# into its own versioned folder, e.g.
#   C:\Program Files\ODA\ODAFileConverter 25.4.0\ODAFileConverter.exe
_ODA_WINDOWS_GLOB = r"C:\Program Files\ODA\ODAFileConverter *\ODAFileConverter.exe"

# Subprocess timeout for the converter run. Generous because ODA is slow on
# large drawings (audit pass + PDF rasterisation).
_ODA_TIMEOUT_SECONDS = 300


def _version_sort_key(path: Path) -> tuple[int, ...]:
    """Extract a numeric tuple from `ODAFileConverter <version>` for sorting.

    Returns `(0,)` for unparseable folder names so they sort last.
    """
    parent = path.parent.name  # e.g. "ODAFileConverter 25.4.0"
    match = re.search(r"(\d+(?:\.\d+)*)", parent)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def _locate_oda_binary() -> Path:
    """Find the ODA File Converter binary or raise FileNotFoundError.

    Lookup order:
      1. `ODA_FILE_CONVERTER` env var (path to the executable).
      2. `shutil.which("ODAFileConverter")` / `ODAFileConverter.exe`.
      3. Glob the default Windows install path; pick the highest version.
    """
    env_value = os.environ.get(_ODA_ENV_VAR)
    if env_value:
        candidate = Path(env_value)
        if candidate.is_file():
            return candidate

    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        which = shutil.which(name)
        if which:
            return Path(which)

    # Windows-default install layout: pick the highest installed version.
    import glob

    matches = [Path(p) for p in glob.glob(_ODA_WINDOWS_GLOB)]
    matches = [p for p in matches if p.is_file()]
    if matches:
        matches.sort(key=_version_sort_key, reverse=True)
        return matches[0]

    raise FileNotFoundError(
        "ODA File Converter binary not found. Set the "
        f"{_ODA_ENV_VAR} environment variable to the executable path, or "
        f"install ODA File Converter from {_ODA_INSTALL_URL} and ensure "
        "ODAFileConverter is on PATH (default install: "
        r"C:\Program Files\ODA\ODAFileConverter <version>\ODAFileConverter.exe)."
    )


def _detect_oda_version(binary: Path) -> str | None:
    """Best-effort: parse `ODAFileConverter --version` output.

    ODA's CLI doesn't reliably support `--version` on every release; we
    swallow any error and return None so this never blocks ingestion.
    """
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    blob = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", blob)
    return match.group(1) if match else None


def _run_oda_conversion(binary: Path, input_dir: Path, output_dir: Path) -> None:
    """Invoke ODA File Converter to convert every DWG in input_dir to PDF.

    CLI signature:
        ODAFileConverter <input_dir> <output_dir> <output_version> \
            <output_type> <recurse> <audit> [<filter>]
    """
    cmd = [
        str(binary),
        str(input_dir),
        str(output_dir),
        "ACAD2018",  # output_version
        "PDF",       # output_type
        "0",         # recurse
        "1",         # audit
        "*.DWG",     # filter
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_ODA_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ODA File Converter timed out after {_ODA_TIMEOUT_SECONDS}s "
            f"(stdout={exc.stdout!r}, stderr={exc.stderr!r})"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"ODA File Converter exited with code {result.returncode}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


def extract(path: Path) -> _Extracted:
    """Convert a DWG to PDF via ODA File Converter, then delegate to the PDF ingester.

    Returns an `_Extracted` with `extraction_method='dwg_converted'` and per-page
    chunks whose `locator` carries the original DWG filename for traceability.

    If the ODA File Converter binary cannot be located (neither via the
    `ODA_FILE_CONVERTER` env var, nor on PATH, nor under the default Windows
    install layout), `_locate_oda_binary` raises `FileNotFoundError` with a
    message pointing at the install URL
    https://www.opendesign.com/guestfiles/oda_file_converter so the dispatcher
    can surface a helpful skip message to the CLI.
    """
    path = Path(path)
    binary = _locate_oda_binary()
    oda_version = _detect_oda_version(binary)

    # Two temp dirs: one staging the input DWG (ODA reads a directory, not a
    # file), one collecting the produced PDF. Both clean up via the context
    # manager, even if the PDF extraction below raises.
    with tempfile.TemporaryDirectory(prefix="meridian_dwg_in_") as in_dir_str, \
         tempfile.TemporaryDirectory(prefix="meridian_dwg_out_") as out_dir_str:
        in_dir = Path(in_dir_str)
        out_dir = Path(out_dir_str)

        # Copy the DWG into the staging dir under its original name so that
        # the produced PDF inherits the same stem.
        staged_dwg = in_dir / path.name
        shutil.copyfile(path, staged_dwg)

        _run_oda_conversion(binary, in_dir, out_dir)

        produced_pdfs = sorted(out_dir.glob("*.pdf")) + sorted(out_dir.glob("*.PDF"))
        if not produced_pdfs:
            raise RuntimeError(
                "ODA File Converter completed but produced no PDF in "
                f"{out_dir}. Input was {path.name}."
            )
        # ODA writes one PDF per input DWG; we staged exactly one input.
        converted_pdf = produced_pdfs[0]

        pdf_result = pdf_ingest.extract(converted_pdf)

    # Override extraction_method, enrich each chunk's locator with the
    # original DWG filename, and merge metadata. text and chunk text
    # carry through unchanged from the PDF result.
    enriched_chunks = []
    for chunk in pdf_result.chunks:
        new_locator: dict[str, Any] = dict(chunk.locator)
        new_locator["converted_from_dwg"] = path.name
        enriched_chunks.append(
            type(chunk)(
                chunk_kind=chunk.chunk_kind,  # stays 'pdf_page'
                locator=new_locator,
                text=chunk.text,
            )
        )

    metadata: dict[str, Any] = dict(pdf_result.metadata)
    metadata["extraction_method"] = "dwg_converted"
    metadata["original_format"] = "dwg"
    metadata["oda_converter_version"] = oda_version
    metadata["converted_pdf_page_count"] = pdf_result.metadata.get("page_count")

    return _Extracted(
        extraction_method="dwg_converted",
        text=pdf_result.text,
        chunks=enriched_chunks,
        metadata=metadata,
    )


__all__ = ["extract"]
