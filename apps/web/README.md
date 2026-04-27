# @meridian/web

Operator UI shell for Meridian. Stub scaffold — only `/` (project status) and `/health` exist today.

## Setup

```bash
cd apps/web
npm install
npm run dev
```

The UI expects the FastAPI backend on `http://localhost:8000`. From the project root:

```bash
uv run uvicorn meridian.api.main:app --reload --port 8000
```

Override the API base by setting `NEXT_PUBLIC_MERIDIAN_API` in `.env.local`. Future sessions will add project create/list, doc import, run, quarantine review, and export.

## Tauri (round-16 scaffold — round-18 build)

Round 16 wired a Tauri 2.x desktop wrapper around this Next.js shell. Today the scaffold is in place; the actual `.msi` build awaits round 18 (Rust toolchain install).

**What's in the repo today:**

- Next.js shell at `apps/web/` (this directory)
- Tauri 2.x crate at `src-tauri/` (sibling of `apps/`)
- Static export wired in `next.config.ts` (`output: "export"`, `trailingSlash: true`, `images.unoptimized: true`)

**What works without Rust:**

```bash
npm run dev      # Next dev server on http://localhost:3000
npm run build    # static export to apps/web/out/
npm run lint
```

`npm run start` is intentionally broken under `output: "export"` (Next 15 errors out — that's expected). Serve `apps/web/out/` with any static server instead.

**What needs the Rust toolchain (round 18):**

```bash
npm run tauri:dev    # live-reload Tauri window pointed at the Next dev server
npm run tauri:build  # produces src-tauri/target/release/bundle/msi/Meridian_0.2.0_x64_en-US.msi
```

**Prereqs to enable Tauri builds:**

- Install [Rust via rustup](https://rustup.rs)
- Install [MSVC Build Tools 2022](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the C++ workload
- WebView2 ships with Windows 11 — no install needed
- WiX Toolset is auto-downloaded by tauri-bundler at build time

**Where the bundled .msi will land:** `src-tauri/target/release/bundle/msi/`.

**Deferred:** code-signing cert (see `docs/DECISIONS.md` §3.4); real icon set lands round 18; FastAPI sidecar spawn (PyInstaller-bundled `meridian-server.exe` launched by Tauri at app start) lands round 17.
