# Installing Meridian

Two paths — pick the one that matches you.

## Path 1 — One-click installer (recommended for non-technical users)

This is the right path if you don't already work with Python, virtual
environments, or terminals. The installer handles everything: Python
install, virtual environment, Meridian package, your Anthropic API key,
desktop shortcut. It takes 5–15 minutes mostly waiting on downloads.

1. Open the [latest release page](https://github.com/profixel660/meridian-trace/releases/latest).
2. Under **Assets**, download **both** files (save them to the same folder,
   e.g. your Desktop):
   - `Install-Meridian.bat`
   - `Install-Meridian.ps1`
3. Right-click `Install-Meridian.bat` → **Run as administrator**.
4. Follow the prompts. The installer will pause and ask for your Anthropic
   API key (paste it — input is hidden).
5. **The installer ends by starting the Meridian backend in the background
   and opening your default browser at the GUI setup wizard**
   (`http://localhost:8000/setup/welcome`). The wizard walks you through
   creating your first project and importing your first documents — point
   it at a folder of PDFs / Word docs / drawings and it picks them up.

   If a browser doesn't open within a few seconds, paste this URL into one
   yourself:

   ```
   http://localhost:8000/setup/welcome
   ```

   If the backend fails to start (rare — usually a port-8000 conflict), the
   installer prints a clear "couldn't start the backend, falling back to
   terminal setup" message and drops you into the legacy `meridian init`
   wizard so you're not stranded.
6. When you're done with the wizard, you can leave the browser open or
   close it — Meridian is now installed. To re-launch later: double-click
   the **Meridian** shortcut on your Desktop, or open PowerShell and run
   `meridian start`.

The first time you launch the installer, **Windows may show a SmartScreen
warning** — click **More info** → **Run anyway**. See `installer/README.md`
in the release for the full explanation. (Code-signing is on the roadmap;
this warning will go away once we sign.)

The backend's process ID is recorded at `C:\Meridian\runtime\backend.pid`
so the uninstaller (and a future `meridian stop` command) can shut it
down cleanly.

To uninstall later, run `Uninstall-Meridian.bat` from the same release
download (or from `C:\Meridian\` if you keep a copy there).

---

## Path 2 — Manual install (developers / CI)

This is the path below if you're comfortable with `python`, `pip`, and
PowerShell — or if you're installing into an existing venv for tooling.

Meridian alpha releases are published as Python wheels on the project's
[GitHub Releases page](https://github.com/profixel660/meridian-trace/releases).
There is no native installer (`.exe` / `.msi`) yet — that arrives with
the §3.7 Tauri packaging round.

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

## Building from source

If you're producing a release wheel yourself (rather than installing one
that's already built), the **Next.js static export must be produced
before `uv build` runs**, because the wheel bundles `apps/web/out/` as
the package data directory `meridian/_web/`. Hatch errors at build time
if `apps/web/out/` doesn't exist.

```bash
# 1. Build the Next.js static export (writes apps/web/out/).
cd apps/web
npm install         # first time only
npm run build       # produces apps/web/out/

# 2. Build the Python wheel from the repo root.
cd ../..
uv build            # produces dist/meridian-X.Y.Z-py3-none-any.whl
```

The built wheel has the GUI wizard's static assets baked in, so a
plain `pip install meridian-X.Y.Z-py3-none-any.whl` is enough — no
separate `npm` step on the install side. The FastAPI backend resolves
`<package>/_web` at runtime to serve the wizard at
`http://localhost:8000/setup/welcome`.

If you forget step 1 and run `uv build` against a fresh checkout, hatch
will fail with `Forced include not found: apps/web/out`. Run `npm run
build` first and retry.

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
