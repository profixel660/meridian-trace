//! Meridian Tauri shell — round 17 will wire `tauri::api::process::CommandChild` here to spawn the bundled `meridian-server.exe` FastAPI sidecar.

/// Boot the Tauri application.
///
/// Registers the standard plugin set the §3.7 plan calls out:
///   * dialog — native file/folder pickers (replaces the browser `<input type="file">`)
///   * shell  — for `shell:allow-open` (open URLs / docs in the OS default app)
///   * fs     — scoped filesystem access from the SPA
///
/// The FastAPI sidecar process is **not** spawned here yet; round 17 owns that.
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|_app| {
            // ROUND-17: spawn FastAPI sidecar here
            // (use tauri_plugin_shell::ShellExt + sidecar("meridian-server") and
            //  manage the CommandChild handle on app state so it can be killed
            //  in the on_window_event(CloseRequested) hook.)
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Meridian Tauri application");
}
