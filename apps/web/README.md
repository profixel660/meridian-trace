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

## Tauri (round-17 scaffold + sidecar wired — round-18 build)

Round 16 wired the Tauri 2.x scaffold; round 17 added the FastAPI sidecar spawn and the `/setup` wizard. Today every line of code is on disk; the actual `.msi` build awaits round 18 (Rust toolchain install).

**What's in the repo today:**

- Next.js shell at `apps/web/` (this directory) — including the round-17 `/setup` wizard pages
- Tauri 2.x crate at `src-tauri/` (sibling of `apps/`) — sidecar spawn + kill-on-close wired in `src/lib.rs`
- Static export wired in `next.config.ts` (`output: "export"`, `trailingSlash: true`, `images.unoptimized: true`)

**What works without Rust:**

```bash
npm run dev      # Next dev server on http://localhost:3000
npm run build    # static export to apps/web/out/ (includes /setup wizard pages)
npm run lint
```

Run a uvicorn backend in a separate terminal to exercise the wizard against real data:

```bash
uv run uvicorn meridian.api.main:app --reload --port 8000
```

`npm run start` is intentionally broken under `output: "export"` (Next 15 errors out — that's expected). Serve `apps/web/out/` with any static server instead.

**What needs the Rust toolchain (round 18):**

```bash
npm run tauri:dev    # live-reload Tauri window — auto-spawns the FastAPI backend (no separate uvicorn step)
npm run tauri:build  # produces src-tauri/target/release/bundle/msi/Meridian_0.2.0_x64_en-US.msi
```

Once Rust is installed (round 18), `npm run tauri:dev` launches the desktop window AND spawns the FastAPI backend automatically — no separate `uvicorn` step needed. The first launch takes ~5s while the backend health-check poll (`127.0.0.1:8000`, max 30s) succeeds. On window close, Tauri kills the spawned backend cleanly via the `WindowEvent::CloseRequested` hook — no orphaned uvicorn processes.

In round-17 dev-mode (no PyInstaller binary on disk yet), Tauri falls back to `python -m uvicorn meridian.api.main:app` from `MERIDIAN_PYTHON` env-var or a `.venv/Scripts/python.exe` lookup. Round 18 replaces the fallback with the bundled `meridian-server.exe`.

**Wizard quickstart:** first launch routes to `/setup` for a 4-step PM-language wizard (API key → first project → first documents → ready). Skip the wizard via direct nav to `/projects` if you'd rather click around the existing UI — the wizard's `/setup/state` is the source of truth, so a partially-completed wizard is resumable.

**Prereqs to enable Tauri builds:**

- Install [Rust via rustup](https://rustup.rs)
- Install [MSVC Build Tools 2022](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the C++ workload
- WebView2 ships with Windows 11 — no install needed
- WiX Toolset is auto-downloaded by tauri-bundler at build time

**Where the bundled .msi will land:** `src-tauri/target/release/bundle/msi/`.

**Deferred:** code-signing cert (see `docs/DECISIONS.md` §3.4); real icon set lands round 18; PyInstaller-bundled `meridian-server.exe` drop-in lands round 18 (replaces round-17's `python -m uvicorn` dev-mode fallback).
