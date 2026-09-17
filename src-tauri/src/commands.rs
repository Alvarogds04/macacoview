use tauri::State;

use crate::collector::{CollectorState, read_state};

/// Tauri command that returns the full state snapshot plus the history.
/// One call per second is sufficient for the frontend.
#[tauri::command]
pub fn get_state(
    state: State<'_, std::sync::Arc<parking_lot::Mutex<CollectorState>>>,
) -> crate::collector::GetState {
    read_state(&state.inner())
}
