# Installing Meridian

Meridian alpha releases are published as Python wheels on the project's
[GitHub Releases page](https://github.com/profixel660/meridian-trace/releases).
There is no installer (`.exe` / `.msi`) yet — that arrives with the §3.7
Tauri packaging round.

## Prerequisites

- **Windows 10/11**, macOS 14+, or Ubuntu 22.04+
- **Python 3.12 or newer**. On Windows, install from
  <https://www.python.org/downloads/> (NOT the Microsoft Store version —
  that ships an alias that confuses many tools). Tick "Add python.exe to
  PATH" during install.
- **Anthropic API key** for extraction calls. Get one at
  <https://console.anthropic.com>.
- **Disk space:** ~500 MB for the venv + dependencies; per-project storage
  is around 50–500 MB depending on document corpus size.

### Windows long-path support (one-time fix)

A few transitive dependencies (notably `litellm`) ship with deeply nested
test fixtures that exceed Windows' default 260-character path limit. If
`pip install` fails with `[Errno 2] No such file or directory` and a very
long path, enable long-path support:

1. Open **PowerShell as Administrator**
2. Run:
   ```powershell
   New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
   ```
3. Restart your terminal.

Alternative: install into a venv whose path is short (e.g. `C:\meridian\venv`
rather than `C:\Users\<long-name>\OneDrive\<...>\venv`).

## Install

1. **Create a fresh virtual environment** (recommended — keeps Meridian's
   dependencies isolated from your other Python projects):

   ```powershell
   # PowerShell on Windows
   python -m venv C:\meridian\venv
   C:\meridian\venv\Scripts\Activate.ps1
   ```

   ```bash
   # bash on macOS / Linux
   python3 -m venv ~/meridian/venv
   source ~/meridian/venv/bin/activate
   ```

2. **Install the latest Meridian wheel from GitHub Releases:**

   ```bash
   pip install https://github.com/profixel660/meridian-trace/releases/latest/download/meridian-0.1.0-py3-none-any.whl
   ```

   (Replace `0.1.0` with the latest version number from the Releases page.
   Future releases will publish as `meridian-X.Y.Z-py3-none-any.whl`.)

3. **Set your Anthropic API key:**

   ```powershell
   # PowerShell — sets for current session only
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```

   ```bash
   # bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

   To make it permanent on Windows, set it via System Properties →
   Environment Variables, OR put it in a `.env` file in the directory
   from which you run `meridian` (Meridian auto-loads `.env` from
   the current directory at startup).

4. **Verify the install:**

   ```bash
   meridian --help
   meridian --version
   ```

5. **Walk the onboarding wizard:**

   ```bash
   meridian init
   ```

   This guides you through: TOTP enrolment (optional), creating your
   first project, importing your first document, and running the
   bootstrap LLM sweep.

## Optional extras

- **License verification** (Ed25519 signature checking — not required for
  local alpha use):

  ```bash
  pip install "meridian[license] @ https://github.com/profixel660/meridian-trace/releases/latest/download/meridian-0.1.0-py3-none-any.whl"
  ```

- **OCR for image-only PDFs** (requires Tesseract installed separately):

  ```bash
  pip install "meridian[ocr] @ https://github.com/profixel660/meridian-trace/releases/latest/download/meridian-0.1.0-py3-none-any.whl"
  ```

  Then install Tesseract — see [_ocr_setup.md in the source tree][ocr-setup]
  for OS-specific steps.

## Upgrading

Meridian has an in-app update check:

```bash
meridian updates check
```

This compares your installed version against the latest release manifest
on GitHub. Once a newer release is available the command prints the
upgrade pip command. (Until the first tagged release exists, `updates
check` returns "up to date" silently — the manifest URL responds 404.)

## Uninstalling

```bash
pip uninstall meridian
```

Project data lives outside the package install, so uninstall does NOT
remove your projects. Project DBs are at `<projects_dir>/*.sqlite` —
delete that directory (or back it up first via
`meridian backup create <project>`) if you also want to remove project
state.

## Web shell (optional)

The Next.js review UI is a separate process. To run it locally:

```bash
# Install Node 22+ from https://nodejs.org/ (or `winget install OpenJS.NodeJS.LTS` on Windows)
git clone https://github.com/profixel660/meridian-trace.git
cd meridian-trace/apps/web
npm install
npm run build
npm start
```

Then in another terminal start the FastAPI backend:

```bash
meridian api  # if implemented; otherwise: uvicorn meridian.api.main:app --port 8000
```

Browse to <http://localhost:3000>. The §3.7 Tauri round will replace
this with a single double-click installer.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for common issues. The
short version:

- **`ModuleNotFoundError: No module named 'meridian'`** — your venv isn't
  activated. Run the `Activate.ps1` / `source activate` step above.
- **`anthropic.AuthenticationError`** — `ANTHROPIC_API_KEY` env var isn't
  set or has been mistyped.
- **`OSError: database is locked`** — another `meridian` process is
  writing to the same project DB. Wait for it to finish or use
  `meridian backup create` to copy the DB safely.

[ocr-setup]: https://github.com/profixel660/meridian-trace/blob/main/src/meridian/ingest/_ocr_setup.md
