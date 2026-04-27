# DWG ingest setup (Windows)

The DWG ingester (`src/meridian/ingest/dwg.py`) handles AutoCAD `.dwg`
drawings by shelling out to the **ODA File Converter** to produce a PDF,
then routing that PDF through the existing PDF ingester. The native
binary is not installable via pip; the user installs it separately, the
same way Tesseract / Poppler are installed for the OCR fallback.

> **Lossy chain.** DWG -> PDF -> PDF-text-extraction preserves textual
> labels, sheet titles and annotation tags well, but **vector geometry
> does not survive** as structured data. Treat the resulting text as a
> best-effort transcription of what is *written* on the drawing, not a
> reconstruction of the model.

## Licensing

ODA File Converter is distributed by the Open Design Alliance and is
**free for non-commercial use**. For commercial use you are responsible
for verifying that your usage falls within the ODA's licence terms; see
the download page for the current terms.

## 1. Install ODA File Converter

1. Download the installer from
   https://www.opendesign.com/guestfiles/oda_file_converter (you'll be
   asked to register a free ODA account first).
2. Run the installer. The default install location on Windows is

   ```
   C:\Program Files\ODA\ODAFileConverter <version>\ODAFileConverter.exe
   ```

   where `<version>` is e.g. `25.4.0`. The ingester globs this pattern
   and picks the highest installed version automatically.

## 2. (Optional) Set `ODA_FILE_CONVERTER` if installed elsewhere

If you've installed the converter to a non-default location, point the
ingester at the executable explicitly:

```powershell
$env:ODA_FILE_CONVERTER = "D:\Tools\ODAFileConverter\ODAFileConverter.exe"
```

To make the setting persistent for new shells:

```powershell
[Environment]::SetEnvironmentVariable(
    "ODA_FILE_CONVERTER",
    "D:\Tools\ODAFileConverter\ODAFileConverter.exe",
    "User"
)
```

## 3. (Alternative) Add ODA to PATH for the current session

If you'd rather rely on PATH lookup than the env var, add the install
folder to PATH for the current PowerShell session:

```powershell
$env:PATH = "C:\Program Files\ODA\ODAFileConverter 25.4.0;$env:PATH"
```

Verify:

```powershell
Get-Command ODAFileConverter
```

## 4. Quick smoke test (without invoking the binary)

```powershell
uv run python -c "from meridian.ingest.dwg import _locate_oda_binary; print(_locate_oda_binary())"
```

If the binary is missing, this raises `FileNotFoundError` with the
install URL in the message; the dispatcher surfaces that to the CLI as
a helpful skip message rather than crashing the run.
