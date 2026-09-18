mod stats;
mod ports;
mod ring_buffer;
mod collector;
mod commands;
mod tokens;

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
