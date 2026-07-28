#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
use tauri::Manager;

/// Port 7549 is Pulse core's single-instance lock (see pulse.py's
/// ensure_single_instance) — something being connectable there means a core
/// process already owns it, whether launched by this installer, a source
/// checkout's run.bat, or a prior run of this same app.
fn backend_already_running() -> bool {
    TcpStream::connect_timeout(&"127.0.0.1:7549".parse().unwrap(), Duration::from_millis(300)).is_ok()
}

fn spawn_backend(resource_dir: PathBuf) {
    let core_dir = resource_dir.join("pulse-core");
    let exe_path = core_dir.join("pulse-core.exe");
    let models_dir = resource_dir.join("models");

    if !exe_path.exists() {
        eprintln!("[Pulse] pulse-core.exe not found at {:?} — cannot start the backend.", exe_path);
        return;
    }

    let mut cmd = Command::new(&exe_path);
    cmd.current_dir(&core_dir);
    // Frozen builds don't reliably land __file__-relative to a sibling "models"
    // folder (PyInstaller's onedir layout nests package files), so this points
    // core/paths.py's models_dir() at the resource-bundled location explicitly.
    // Running from source never sets this, so that behavior is unaffected.
    cmd.env("PULSE_MODELS_DIR", &models_dir);
    cmd.env("HF_HUB_OFFLINE", "1");
    cmd.env("TRANSFORMERS_OFFLINE", "1");

    match cmd.spawn() {
        Ok(_) => println!("[Pulse] Backend starting (models: {:?}).", models_dir),
        Err(e) => eprintln!("[Pulse] Failed to start backend: {e}"),
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let resource_dir = app.path().resource_dir().expect("failed to resolve resource dir");
            std::thread::spawn(move || {
                if backend_already_running() {
                    println!("[Pulse] Backend already running — not starting a second copy.");
                } else {
                    spawn_backend(resource_dir);
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Pulse UI");
}
