//! Standalone HTTP daemon.
//!
//! Runs the same collectors as the Tauri app and serves the dashboard state
//! over HTTP on `127.0.0.1`. With a compiled frontend (`dist/`) it serves the
//! full UI; without one, `/api/state` still works and the root page explains
//! how to build the frontend.
//!
//! Port: `PC_AI_PORT`, default 8787.
//!
//! Usage: `PC_AI_PORT=8787 macacoview-serve`, then open
//! `http://localhost:8787`.

fn main() -> std::io::Result<()> {
    pc_ai_monitor_lib::serve::run_daemon()
}
