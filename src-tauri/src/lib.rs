mod macos;

// The `[[watch]]` config is consumed by the macOS collector; on Linux the
// Python collector (`scripts/macacoview-stats`) reads the same file itself, so
// outside macOS the module exists to run its tests.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
mod config;

mod stats;
mod ports;
mod ring_buffer;
mod collector;
mod commands;
mod tokens;
mod inventory;

/// Standalone HTTP daemon that serves `/api/state` (and, with a compiled
/// frontend, the dashboard assets) over `127.0.0.1`. See [`serve`].
pub mod serve;

use collector::start_sampling_loop;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Start the sampling loop on a background thread
    let state = start_sampling_loop();

    tauri::Builder::default()
        .manage(state)
        .invoke_handler(tauri::generate_handler![commands::get_state])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
