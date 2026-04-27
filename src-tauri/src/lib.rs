//! Meridian Tauri shell — round 17 wires the FastAPI sidecar spawn.
//!
//! Two spawn paths, chosen at runtime (no feature-flag gating):
//!
//! 1. **Sidecar path** (round 18 / production): `tauri_plugin_shell::ShellExt`
//!    looks up `meridian-server` declared in `tauri.conf.json` →
//!    `bundle.externalBin`. The PyInstaller build that round 18 produces drops
//!    a binary at `src-tauri/binaries/meridian-server-x86_64-pc-windows-msvc.exe`
//!    and the Tauri bundler ships it next to the .exe. When that file exists,
//!    `Command::sidecar("meridian-server")` succeeds and we use it.
//!
//! 2. **Dev fallback** (round 17): If no sidecar binary is present we spawn
//!    `python -m uvicorn meridian.api.main:app --host 127.0.0.1 --port 8000`
//!    using the workspace's `.venv` interpreter. This keeps `npm run tauri:dev`
//!    working today, before round 18 produces a PyInstaller artifact.
//!
//! Lifecycle:
//!   * The `CommandChild` handle is stored on app state via
//!     `App::manage(BackendChild { ... })`.
//!   * On `WindowEvent::CloseRequested` we drain the handle and call `kill()`
//!     fire-and-forget. Idempotent because we `Option::take()` the handle.
//!
//! Health-gate:
//!   * Before letting the window load, poll `127.0.0.1:8000` with a TCP
//!     connect (no new dep — `std::net::TcpStream`) up to 30s @ 200ms. If the
//!     port never opens we still let the window open; the SPA's
//!     `ApiErrorPanel` surfaces the connection failure.

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Backend host/port — must match the FastAPI default in `src/meridian/api/main.py`.
const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8000;

/// Health-gate poll budget — short interval, generous total budget.
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(200);
const HEALTH_POLL_TIMEOUT: Duration = Duration::from_secs(30);

/// App-managed handle to the spawned backend process.
///
/// Wrapped in `Mutex<Option<...>>` so the close hook can `take()` the child
/// out and call `kill()` exactly once even if `CloseRequested` fires twice.
struct BackendChild(Mutex<Option<CommandChild>>);

impl BackendChild {
    fn new(child: CommandChild) -> Self {
        Self(Mutex::new(Some(child)))
    }

    /// Idempotent: takes the child out of the slot. Calling twice is a no-op
    /// the second time.
    fn kill_if_alive(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(child) = guard.take() {
                // Fire-and-forget — don't block the close on the result.
                let _ = child.kill();
            }
        }
    }
}

/// Boot the Tauri application.
///
/// Registers the §3.7 plan's plugin set:
///   * dialog — native file/folder pickers
///   * shell  — `shell:allow-open` AND the `Command` spawn surface used here
///   * fs     — scoped filesystem access from the SPA
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            // Spawn whichever backend we can reach. Failures here are logged
            // but non-fatal — the window still opens so the user sees a real
            // error in the SPA rather than a blank Tauri shell.
            match spawn_backend(app.handle()) {
                Ok(child) => {
                    app.manage(BackendChild::new(child));
                    eprintln!("[meridian] backend spawn: ok, polling health…");
                    wait_for_backend_ready();
                }
                Err(e) => {
                    eprintln!("[meridian] backend spawn FAILED: {e}");
                    eprintln!("[meridian] window will open without a live backend; SPA will show ApiErrorPanel.");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Meridian Tauri application")
        .run(|app_handle, event| {
            // Window close → kill the backend. Use RunEvent::WindowEvent so we
            // catch close on the main window before the app fully exits.
            if let RunEvent::WindowEvent {
                event: WindowEvent::CloseRequested { .. },
                ..
            } = event
            {
                if let Some(child_state) = app_handle.try_state::<BackendChild>() {
                    eprintln!("[meridian] window CloseRequested → killing backend child");
                    child_state.kill_if_alive();
                }
            }
            // Belt-and-braces: also kill on full app exit.
            if let RunEvent::Exit = event {
                if let Some(child_state) = app_handle.try_state::<BackendChild>() {
                    child_state.kill_if_alive();
                }
            }
        });
}

/// Try the sidecar path first; if that errors (binary not present, e.g. round
/// 17 dev state), fall back to dev-mode `python -m uvicorn`.
fn spawn_backend(app: &tauri::AppHandle) -> Result<CommandChild, String> {
    // ROUND-18: This sidecar lookup is the production path. Once PyInstaller
    // produces `src-tauri/binaries/meridian-server-x86_64-pc-windows-msvc.exe`
    // and `tauri.conf.json` lists it in `bundle.externalBin`, this branch wins
    // and the dev fallback below is dead code on installed machines.
    match try_spawn_sidecar(app) {
        Ok(child) => {
            eprintln!("[meridian] spawn: using bundled sidecar (meridian-server)");
            return Ok(child);
        }
        Err(e) => {
            eprintln!("[meridian] sidecar unavailable ({e}); falling back to dev-mode uvicorn");
        }
    }
    try_spawn_dev_uvicorn(app)
}

/// Spawn the PyInstaller-bundled sidecar declared as `binaries/meridian-server`
/// in `tauri.conf.json` → `bundle.externalBin`.
fn try_spawn_sidecar(app: &tauri::AppHandle) -> Result<CommandChild, String> {
    let cmd = app
        .shell()
        .sidecar("meridian-server")
        .map_err(|e| format!("sidecar lookup failed: {e}"))?;
    spawn_with_logging(cmd, "sidecar")
}

/// Dev-mode fallback: spawn `<python> -m uvicorn meridian.api.main:app …`.
///
/// Python interpreter resolution order:
///   1. `MERIDIAN_PYTHON` env var (explicit override).
///   2. `<workspace>/.venv/Scripts/python.exe` on Windows (parent of `src-tauri/`).
///   3. Bare `python` on PATH (will fail loudly if not installed).
fn try_spawn_dev_uvicorn(app: &tauri::AppHandle) -> Result<CommandChild, String> {
    let python = resolve_dev_python();
    eprintln!("[meridian] dev-mode python: {}", python.display());

    let cmd = app
        .shell()
        .command(python.to_string_lossy().to_string())
        .args([
            "-m",
            "uvicorn",
            "meridian.api.main:app",
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
        ]);

    spawn_with_logging(cmd, "uvicorn")
}

/// Resolve the dev-mode Python interpreter. See `try_spawn_dev_uvicorn` for
/// the precedence order.
fn resolve_dev_python() -> PathBuf {
    if let Ok(explicit) = std::env::var("MERIDIAN_PYTHON") {
        if !explicit.is_empty() {
            return PathBuf::from(explicit);
        }
    }

    // Workspace root = parent of the `src-tauri/` dir at compile time.
    // CARGO_MANIFEST_DIR is `<workspace>/src-tauri` for this crate.
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(workspace) = manifest_dir.parent() {
        #[cfg(windows)]
        let venv_python = workspace.join(".venv").join("Scripts").join("python.exe");
        #[cfg(not(windows))]
        let venv_python = workspace.join(".venv").join("bin").join("python");

        if venv_python.exists() {
            return venv_python;
        }
    }

    // Last resort — `python` on PATH. Will error at spawn-time if absent.
    PathBuf::from("python")
}

/// Spawn a `tauri_plugin_shell::Command`, attach a stdout/stderr pump that
/// echoes lines to the Tauri host's stderr with a `[meridian:<tag>]` prefix.
fn spawn_with_logging(
    cmd: tauri_plugin_shell::process::Command,
    tag: &'static str,
) -> Result<CommandChild, String> {
    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    // Pump child stdout/stderr → host stderr in a Tokio task so the launch
    // log shows uvicorn's startup banner / errors live.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    eprintln!("[meridian:{tag}:out] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[meridian:{tag}:err] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Error(err) => {
                    eprintln!("[meridian:{tag}:!!] {err}");
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!(
                        "[meridian:{tag}] terminated code={:?} signal={:?}",
                        payload.code, payload.signal
                    );
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(child)
}

/// Poll `127.0.0.1:8000` with a TCP connect until the port is open or the
/// budget elapses. A successful TCP connect is a sufficient proxy for "uvicorn
/// is bound" — round 19 can swap this for a real `GET /health` if needed.
fn wait_for_backend_ready() {
    let addr = format!("{BACKEND_HOST}:{BACKEND_PORT}");
    let deadline = Instant::now() + HEALTH_POLL_TIMEOUT;
    let mut attempts = 0u32;

    loop {
        attempts += 1;
        match std::net::TcpStream::connect_timeout(
            &addr.parse().expect("backend addr is a static valid SocketAddr"),
            HEALTH_POLL_INTERVAL,
        ) {
            Ok(_) => {
                eprintln!(
                    "[meridian] backend ready on {addr} after {attempts} attempt(s)"
                );
                return;
            }
            Err(_) if Instant::now() < deadline => {
                std::thread::sleep(HEALTH_POLL_INTERVAL);
            }
            Err(e) => {
                eprintln!(
                    "[meridian] backend NOT ready on {addr} after {attempts} attempt(s): {e}"
                );
                eprintln!(
                    "[meridian] proceeding to open window anyway; SPA will surface the failure"
                );
                return;
            }
        }
    }
}
