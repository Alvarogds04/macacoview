use std::process::Command;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use parking_lot::Mutex;
use serde::Serialize;

use crate::ports::{parse_ss_output, PortRow};
use crate::ring_buffer::{sample_from_stats, HistorySample, RingBuffer, RING_BUFFER_CAPACITY};
use crate::stats::{Groups, Memory, Model, Process, Stats};
use crate::tokens::{collect_tokens, Tokens};

/// Token counters come from Prometheus, every llama.cpp server, a transcript
/// scan and codexbar: too heavy for the one-second loop. They refresh on their
/// own cadence and the last value is carried into every snapshot.
const TOKENS_INTERVAL_SECS: u64 = 10;

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

/// The most recent complete snapshot.
#[derive(Debug, Clone, Serialize)]
pub struct FullSnapshot {
    pub memory: Memory,
    pub models: Vec<Model>,
    pub groups: Groups,
    pub processes: Vec<Process>,
    pub ports: Vec<PortRow>,
    pub tokens: Option<Tokens>,
}

/// The payload returned by the `get_state` command.
#[derive(Debug, Clone, Serialize)]
pub struct GetState {
    pub snapshot: Option<FullSnapshot>,
    pub history: Vec<HistorySample>,
}

/// Shared state protected by a mutex.
pub struct CollectorState {
    snapshot: Option<FullSnapshot>,
    history: RingBuffer<HistorySample>,
    tokens: Option<Tokens>,
}

impl CollectorState {
    fn new() -> Self {
        Self {
            snapshot: None,
            history: RingBuffer::new(RING_BUFFER_CAPACITY),
            tokens: None,
        }
    }

    /// Store one collected tick. Deliberately tiny: it must never hold the
    /// lock while a subprocess runs, or the frontend's once-per-second
    /// `get_state` call would stall for the whole collection.
    fn apply(&mut self, stats: Stats, ports: Vec<PortRow>) {
        self.history.push(sample_from_stats(
            stats.memory.used_gib,
            stats.memory.available_gib,
            &stats.groups,
        ));

        self.snapshot = Some(FullSnapshot {
            memory: stats.memory,
            models: stats.models,
            groups: stats.groups,
            processes: stats.processes,
            ports,
            tokens: self.tokens.clone(),
        });
    }

    /// Publish fresh token counters. They land on the stored snapshot too, so
    /// the tab is populated immediately instead of waiting up to a second.
    fn set_tokens(&mut self, tokens: Tokens) {
        self.tokens = Some(tokens.clone());
        if let Some(snapshot) = self.snapshot.as_mut() {
            snapshot.tokens = Some(tokens);
        }
    }

    pub fn get_state(&self) -> GetState {
        GetState {
            snapshot: self.snapshot.clone(),
            history: self.history.samples().to_vec(),
        }
    }
}

// ---------------------------------------------------------------------------
// Collection (runs outside the lock)
// ---------------------------------------------------------------------------

fn collect_stats() -> Result<Stats, String> {
    let output = Command::new(pc_ai_stats_bin())
        .output()
        .map_err(|e| format!("pc-ai-stats: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "pc-ai-stats exited with {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    let json = String::from_utf8(output.stdout)
        .map_err(|e| format!("pc-ai-stats output not UTF-8: {e}"))?;

    serde_json::from_str(&json).map_err(|e| format!("pc-ai-stats JSON parse error: {e}"))
}

/// Read the privileged listener table. The helper is invoked with an argv
/// array only: no shell, no interpolation, and no option beyond `-n`.
/// The collectors ship under $HOME/.local/bin; hard-coding a home directory is
/// what made the suite pass on exactly one machine.
fn pc_ai_stats_bin() -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    format!("{home}/.local/bin/pc-ai-stats")
}

fn collect_ports() -> Result<Vec<PortRow>, String> {
    let output = Command::new("sudo")
        .args(["-n", "/usr/local/bin/pc-ai-ports-read"])
        .output()
        .map_err(|e| format!("sudo pc-ai-ports-read: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "pc-ai-ports-read exited with {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    let text = String::from_utf8(output.stdout)
        .map_err(|e| format!("ports output not UTF-8: {e}"))?;

    Ok(parse_ss_output(&text))
}

/// Collect one tick and publish it. Any failure is logged and skipped; the
/// sampling thread keeps running so a transient error never stops the UI.
fn run_tick(state: &Arc<Mutex<CollectorState>>) {
    let stats = match collect_stats() {
        Ok(stats) => stats,
        Err(error) => {
            eprintln!("collector: stats collection failed: {error}");
            return;
        }
    };

    // A failing privileged read degrades to an empty table rather than
    // discarding otherwise valid system data.
    let ports = match collect_ports() {
        Ok(ports) => ports,
        Err(error) => {
            eprintln!("collector: ports collection failed: {error}");
            Vec::new()
        }
    };

    state.lock().apply(stats, ports);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Start the one-second sampling loop on a background thread and return the
/// shared state handle to register with Tauri.
pub fn start_sampling_loop() -> Arc<Mutex<CollectorState>> {
    let state = Arc::new(Mutex::new(CollectorState::new()));

    thread::spawn({
        let state = Arc::clone(&state);
        move || {
            let mut tick: u64 = 0;
            loop {
                run_tick(&state);

                // Tick zero collects too, so the tab fills on startup.
                if tick.is_multiple_of(TOKENS_INTERVAL_SECS) {
                    match collect_tokens() {
                        Ok(tokens) => state.lock().set_tokens(tokens),
                        // Keep the previous value: a transient failure must not
                        // blank a section that was showing real numbers.
                        Err(error) => {
                            eprintln!("collector: tokens collection failed: {error}")
                        }
                    }
                }

                tick += 1;
                thread::sleep(Duration::from_secs(1));
            }
        }
    });

    state
}

/// Read the current snapshot plus history.
pub fn read_state(state: &Arc<Mutex<CollectorState>>) -> GetState {
    state.lock().get_state()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    const BASELINE: &str =
        concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/ports_ss.txt");

    #[test]
    fn test_collect_ports_from_baseline() {
        let text = std::fs::read_to_string(BASELINE).expect("baseline file should be readable");
        let rows = parse_ss_output(&text);
        assert!(!rows.is_empty(), "should have parsed rows from baseline");

        let abito = rows
            .iter()
            .find(|r| r.port == 11002)
            .expect("port 11002 must be present");
        assert_eq!(abito.service_name, "Abito-gpt");
    }

    #[test]
    fn test_apply_stores_snapshot_and_history() {
        let mut state = CollectorState::new();
        let stats: Stats = serde_json::from_str(
            r#"{
                "memory": {"total_gib": 128.0, "used_gib": 40.0, "available_gib": 88.0,
                           "swap_total_gib": 8.0, "swap_used_gib": 0.0},
                "models": [],
                "groups": {"pi": {"rss_gib": 2.5, "cpu": 1.0, "pids": [1]}},
                "processes": []
            }"#,
        )
        .expect("fixture must parse");

        state.apply(stats, Vec::new());

        let snapshot = state.snapshot.as_ref().expect("snapshot must be stored");
        assert_eq!(snapshot.memory.used_gib, 40.0);
        assert!(snapshot.ports.is_empty());

        let history = state.history.samples();
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].used_gib, 40.0);
    }

    #[test]
    fn test_set_tokens_reaches_snapshot() {
        // Token counters arrive on their own cadence: they must show up on the
        // next snapshot without waiting for a new stats collection.
        let mut state = CollectorState::new();
        let stats: Stats = serde_json::from_str(
            r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
                "swap_total_gib": 0.0, "swap_used_gib": 0.0},
                "models": [], "groups": {}, "processes": []}"#,
        )
        .expect("fixture must parse");

        state.apply(stats.clone(), Vec::new());
        assert!(state.snapshot.as_ref().unwrap().tokens.is_none());

        let tokens: Tokens = serde_json::from_str(
            r#"{"remote": {"status": "ok", "total": 42}}"#,
        )
        .expect("tokens fixture must parse");
        state.set_tokens(tokens);

        assert_eq!(state.snapshot.as_ref().unwrap().tokens.as_ref().unwrap().remote.total, 42);

        // And they survive the next stats tick.
        state.apply(stats, Vec::new());
        assert_eq!(state.snapshot.as_ref().unwrap().tokens.as_ref().unwrap().remote.total, 42);
    }

    #[test]
    fn test_apply_is_cheap_and_does_not_hold_lock_during_collection() {
        // Storage must be fast because the sampling thread calls it while the
        // frontend may be reading state. Collection happens outside the lock.
        let mut state = CollectorState::new();
        let stats: Stats = serde_json::from_str(
            r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
                "swap_total_gib": 0.0, "swap_used_gib": 0.0},
                "models": [], "groups": {}, "processes": []}"#,
        )
        .expect("fixture must parse");

        let start = Instant::now();
        for _ in 0..300 {
            state.apply(stats.clone(), Vec::new());
        }
        let elapsed = start.elapsed();

        assert_eq!(state.history.samples().len(), RING_BUFFER_CAPACITY);
        assert!(
            elapsed.as_millis() < 100,
            "300 applies should be near-instant, took {elapsed:?}"
        );
    }
}
