# `src-tauri/` — Meridian desktop shell

This directory contains the Tauri 2.x scaffold that wraps the Next.js SPA in `apps/web/` into a Windows `.msi` installer. See `docs/DECISIONS.md` §3.7 for the rationale (Path A — D-static via Tauri).

## Prerequisites to build

You only need these on machines that actually compile the desktop binary; pure web development against `apps/web/` does not require any of this.

1. **Rust toolchain** — install via [`rustup`](https://rustup.rs/). Stable channel, MSVC target (`x86_64-pc-windows-msvc`) is the default on Windows.
2. **MSVC Build Tools 2022** — install "Desktop development with C++" workload from the Visual Studio Installer. The Tauri 2 build needs `link.exe` and the Windows 10/11 SDK.
3. **WebView2 runtime** — pre-installed on Windows 11 (and on Windows 10 since the April 2022 updates), so this is a no-op for the SME's machine. The `.msi` installer ships a runtime-installer fallback for older systems.
4. **Node.js 20.x + npm** — same as for `apps/web/`.

## Daily workflow

All commands run from `apps/web/` (the `tauri` CLI is wired up there):

```bash
# dev — hot-reloading SPA inside a Tauri window, talks to localhost:3000
npm run tauri:dev

# build — produces the .msi installer
npm run tauri:build
```

The `.msi` lands at:

```
src-tauri/target/release/bundle/msi/Meridian_<version>_x64_en-US.msi
```

## What's deferred

Round 16 deliberately scoped this scaffold tight. Future rounds own:

- **Code signing** (`docs/DECISIONS.md` §3.4) — Authenticode certificate, `digestAlgorithm` / `certificateThumbprint` / `timestampUrl` in `tauri.conf.json` `bundle.windows.wix`. Without this the SME hits SmartScreen on first install.
- **Real icons** (round 18) — currently `icons/` only holds a `README.md`; `tauri build` will fail until the PNG/ICO/ICNS set is generated via `npm run tauri icon`.
- **Sidecar spawn** (round 17) — `lib.rs` has a `// ROUND-17` comment marking where the FastAPI `meridian-server.exe` sidecar will be launched from `setup()`.
- **Plugin permission narrowing** (round 17) — `capabilities/default.json` currently grants `fs:default` and `shell:allow-open` broadly. Round 17 should scope `fs:` to the per-project directory once that path resolver lands.
