#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use rand::RngCore;
use tauri::{Emitter, Manager, State};

fn project_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent")
        .to_path_buf()
}

fn find_python() -> PathBuf {
    let root = project_root();
    let venv = root.join("python/.venv/bin/python3");
    if venv.exists() {
        return venv;
    }
    PathBuf::from("python3")
}

fn server_script() -> PathBuf {
    project_root().join("python/server.py")
}

struct SidecarState {
    port: Mutex<Option<u16>>,
    process: Mutex<Option<Child>>,
    token: String,
}

/// Shared wrapper so the monitor thread can access the same SidecarState
/// that Tauri manages. Tauri's `State<T>` is backed by an Arc internally,
/// but we need our own Arc to hand to the std::thread we spawn.
struct SharedSidecar(Arc<SidecarState>);

impl std::ops::Deref for SharedSidecar {
    type Target = SidecarState;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

#[tauri::command]
fn get_sidecar_port(state: State<SharedSidecar>) -> Result<u16, String> {
    state
        .port
        .lock()
        .map_err(|e| format!("Failed to lock port state: {}", e))?
        .ok_or_else(|| "Sidecar port not available yet".to_string())
}

#[tauri::command]
fn get_sidecar_token(state: State<SharedSidecar>) -> Result<String, String> {
    Ok(state.token.clone())
}

#[tauri::command]
fn start_python_sidecar(state: State<SharedSidecar>) -> Result<u16, String> {
    {
        let port_guard = state
            .port
            .lock()
            .map_err(|e| format!("Failed to lock port state: {}", e))?;
        if let Some(p) = *port_guard {
            return Ok(p);
        }
    }

    let port = portpicker::pick_unused_port().ok_or("No free port available")?;

    let child = Command::new(find_python())
        .arg(server_script())
        .arg("--port")
        .arg(port.to_string())
        .env("XRADAR_SESSION_TOKEN", &state.token)
        .spawn()
        .map_err(|e| format!("Failed to start Python sidecar: {}", e))?;

    *state
        .port
        .lock()
        .map_err(|e| format!("Failed to lock port state: {}", e))? = Some(port);
    *state
        .process
        .lock()
        .map_err(|e| format!("Failed to lock process state: {}", e))? = Some(child);

    Ok(port)
}

#[tauri::command]
fn open_file_dialog(path: String) -> Result<String, String> {
    let path_buf = std::path::PathBuf::from(&path);
    if path_buf.exists() {
        Ok(path)
    } else {
        Err(format!("File not found: {}", path))
    }
}

/// Health check command the frontend can call to verify the sidecar is alive.
/// Returns the port if the process is running, or an error if it has exited or
/// was never started.
#[tauri::command]
fn sidecar_health_check(state: State<SharedSidecar>) -> Result<u16, String> {
    let port = state
        .port
        .lock()
        .map_err(|e| format!("Failed to lock port state: {}", e))?
        .ok_or_else(|| "Sidecar was never started".to_string())?;

    let mut proc_guard = state
        .process
        .lock()
        .map_err(|e| format!("Failed to lock process state: {}", e))?;

    match proc_guard.as_mut() {
        None => Err("Sidecar process handle is missing".to_string()),
        Some(child) => match child.try_wait() {
            Ok(Some(status)) => Err(format!("Sidecar process exited with status: {}", status)),
            Ok(None) => Ok(port), // still running
            Err(e) => Err(format!("Failed to query sidecar process status: {}", e)),
        },
    }
}

fn generate_sidecar_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

fn spawn_sidecar(port: u16, token: &str) -> Result<Child, String> {
    let python = find_python();
    let script = server_script();

    eprintln!(
        "Starting Python sidecar: {} {} --port {}",
        python.display(),
        script.display(),
        port
    );

    let mut child = Command::new(&python)
        .arg(&script)
        .arg("--port")
        .arg(port.to_string())
        .env("XRADAR_SESSION_TOKEN", token)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| {
            format!(
                "Failed to spawn Python sidecar: {} (python={}, script={})",
                e,
                python.display(),
                script.display()
            )
        })?;

    // Wait for the READY:<port> marker from stdout
    let stdout = child
        .stdout
        .take()
        .ok_or("Failed to capture Python stdout")?;
    let reader = BufReader::new(stdout);
    let timeout = Duration::from_secs(30);
    let start = std::time::Instant::now();
    let mut ready = false;

    for line in reader.lines() {
        if start.elapsed() > timeout {
            break;
        }
        match line {
            Ok(text) => {
                eprintln!("[python stdout] {}", text);
                if text.starts_with("READY:") {
                    eprintln!("Python sidecar ready on port {}", port);
                    ready = true;
                    break;
                }
            }
            Err(e) => {
                eprintln!("Error reading Python stdout: {}", e);
                break;
            }
        }
    }

    if !ready {
        match child.try_wait() {
            Ok(Some(status)) => {
                return Err(format!(
                    "Python sidecar exited prematurely with status: {}",
                    status
                ));
            }
            Ok(None) => {
                // Process is alive but never sent READY -- kill it and return an error.
                let _ = child.kill();
                return Err(
                    "Python sidecar did not send READY signal within 30 seconds; process killed"
                        .to_string(),
                );
            }
            Err(e) => {
                return Err(format!("Failed to check Python process status: {}", e));
            }
        }
    }

    Ok(child)
}

/// Background monitor that checks if the sidecar is still alive every 5 seconds.
/// If the process has exited, it automatically restarts it on the same port.
fn start_sidecar_monitor(state: Arc<SidecarState>) {
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(Duration::from_secs(5));

            let port = {
                match state.port.lock() {
                    Ok(guard) => match *guard {
                        Some(p) => p,
                        None => continue, // sidecar was never started, nothing to monitor
                    },
                    Err(e) => {
                        eprintln!("[sidecar-monitor] Failed to lock port state: {}", e);
                        continue;
                    }
                }
            };

            let needs_restart = {
                match state.process.lock() {
                    Ok(mut guard) => match guard.as_mut() {
                        Some(child) => match child.try_wait() {
                            Ok(Some(status)) => {
                                eprintln!(
                                    "[sidecar-monitor] Sidecar exited with status: {}. Restarting...",
                                    status
                                );
                                true
                            }
                            Ok(None) => false, // still running
                            Err(e) => {
                                eprintln!(
                                    "[sidecar-monitor] Error checking process status: {}",
                                    e
                                );
                                false
                            }
                        },
                        None => {
                            // No process handle but we have a port -- try to restart
                            eprintln!("[sidecar-monitor] No process handle found. Restarting...");
                            true
                        }
                    },
                    Err(e) => {
                        eprintln!("[sidecar-monitor] Failed to lock process state: {}", e);
                        continue;
                    }
                }
            };

            if needs_restart {
                match spawn_sidecar(port, &state.token) {
                    Ok(child) => {
                        if let Ok(mut guard) = state.process.lock() {
                            *guard = Some(child);
                        }
                        eprintln!(
                            "[sidecar-monitor] Sidecar restarted successfully on port {}",
                            port
                        );
                    }
                    Err(e) => {
                        eprintln!("[sidecar-monitor] Failed to restart sidecar: {}", e);
                    }
                }
            }
        }
    });
}

fn main() {
    let shared = Arc::new(SidecarState {
        port: Mutex::new(None),
        process: Mutex::new(None),
        token: generate_sidecar_token(),
    });

    // Clone for the monitor thread (will be started in setup)
    let monitor_handle = Arc::clone(&shared);

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SharedSidecar(Arc::clone(&shared)))
        .setup(move |app| {
            let state_arc = Arc::clone(&app.state::<SharedSidecar>().0);
            let app_handle = app.handle().clone();

            // Delete stale port file from previous runs so the frontend doesn't
            // connect to a dead port while we're spawning the new sidecar.
            let stale_port_file = project_root().join("public/.sidecar-port");
            let _ = std::fs::remove_file(&stale_port_file);

            // Spawn the sidecar on a background thread so the window appears instantly.
            // Emits "sidecar-ready" event to the frontend when the port is available.
            std::thread::spawn(move || {
                let port = match portpicker::pick_unused_port() {
                    Some(p) => p,
                    None => {
                        eprintln!("No free port available for sidecar");
                        return;
                    }
                };

                let token = state_arc.token.clone();
                match spawn_sidecar(port, &token) {
                    Ok(child) => {
                        if let Ok(mut guard) = state_arc.port.lock() {
                            *guard = Some(port);
                        }
                        if let Ok(mut guard) = state_arc.process.lock() {
                            *guard = Some(child);
                        }
                        eprintln!("Python sidecar started on port {}", port);

                        // Notify the frontend via event — more reliable than polling
                        if let Err(e) = app_handle.emit("sidecar-ready", port) {
                            eprintln!("Failed to emit sidecar-ready event: {}", e);
                        }

                        // Also write port to a file the frontend can fetch via Vite
                        let port_file = project_root().join("public/.sidecar-port");
                        if let Err(e) = std::fs::write(&port_file, port.to_string()) {
                            eprintln!("Failed to write port file: {}", e);
                        } else {
                            eprintln!("Wrote port {} to {}", port, port_file.display());
                        }
                    }
                    Err(e) => {
                        eprintln!("Warning: Failed to start Python sidecar: {}", e);
                    }
                }
            });

            // Start the background monitor thread
            start_sidecar_monitor(monitor_handle);

            // Auto-open devtools in debug builds
            #[cfg(debug_assertions)]
            if let Some(window) = app.get_webview_window("main") {
                window.open_devtools();
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_python_sidecar,
            get_sidecar_port,
            get_sidecar_token,
            open_file_dialog,
            sidecar_health_check,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
