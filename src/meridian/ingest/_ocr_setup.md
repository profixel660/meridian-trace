# OCR fallback setup (Windows)

The PDF ingester (`src/meridian/ingest/pdf.py`) automatically falls back to
OCR when a PDF has very little extractable text per page (a strong signal it
is a scanned/image-only document). The OCR path uses two third-party native
binaries that are NOT installable via pip: **Tesseract** (OCR engine) and
**Poppler** (PDF rasteriser used by `pdf2image`).

The Python wrappers (`pytesseract`, `pdf2image`, `Pillow`) are declared in
the `[ocr]` optional-dependency group in `pyproject.toml` and are installed
with:

```powershell
uv sync --extra ocr
```

To disable the OCR fallback at runtime (returns the thin text-extraction
result instead of raising), set:

```powershell
$env:MERIDIAN_OCR_DISABLED = "1"
```

If the binaries below are not installed, the ingester catches the error and
returns the text-only result with `metadata["ocr_skipped_reason"]` populated;
nothing crashes.

---

## 1. Install Tesseract OCR

The maintained Windows build is the UB-Mannheim installer.

1. Download the installer from
   https://github.com/UB-Mannheim/tesseract/wiki (pick the latest 64-bit
   `tesseract-ocr-w64-setup-*.exe`).
2. Run the installer. The default install location is
   `C:\Program Files\Tesseract-OCR`.
3. During install, enable **"Add to PATH"** for the current user (or add
   `C:\Program Files\Tesseract-OCR` to `PATH` manually afterwards).
4. Verify in a fresh terminal:

   ```powershell
   tesseract --version
   ```

If `tesseract` is not on `PATH` but installed elsewhere, set the binary
explicitly before importing the ingester:

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## 2. Install Poppler (for `pdf2image`)

`pdf2image` shells out to Poppler's `pdftoppm` / `pdfinfo` to rasterise PDF
pages. There is no official Windows installer; use the conda-forge or
`oschwartz10612` Windows build.

1. Download the latest release ZIP from
   https://github.com/oschwartz10612/poppler-windows/releases (e.g.
   `Release-24.xx.x-0.zip`).
2. Extract to a stable location, e.g. `C:\poppler\`. You should now have
   `C:\poppler\Library\bin\pdftoppm.exe`.
3. Add `C:\poppler\Library\bin` to your `PATH` environment variable.
4. Verify in a fresh terminal:

   ```powershell
   pdftoppm -v
   pdfinfo -v
   ```

If you'd rather not modify `PATH`, you can pass the binary path per call —
but the current ingester does not surface that hook, so PATH is the simpler
route on Windows.

## 3. Quick smoke test

```powershell
uv run python -c "import pytesseract, pdf2image; print('ocr deps importable')"
```

If both binaries are on PATH, the ingester will OCR scanned PDFs
automatically; otherwise the relevant `TesseractNotFoundError` /
`PDFInfoNotInstalledError` is caught and recorded in
`metadata['ocr_skipped_reason']`.
