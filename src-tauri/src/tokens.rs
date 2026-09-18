//! Token consumption per source.
//!
//! The aggregation lives in `scripts/pc-ai-tokens` (installed as
//! `/home/alvaro/.local/bin/pc-ai-tokens`), which talks to Prometheus,
//! llama.cpp, the Pi session transcripts and codexbar. This module only runs it
//! and deserializes the document: keeping the four sources out of the Rust tree
//! avoids adding an HTTP client to an app that needs none.
//!
//! Every section carries its own `status`, so one unavailable source degrades
//! that section instead of the whole snapshot. Missing fields default, which is
//! what makes a partially filled document still deserialize.

use std::process::Command;

use serde::{Deserialize, Serialize};

const TOKENS_BIN: &str = "/home/alvaro/.local/bin/pc-ai-tokens";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct RemoteModel {
    pub name: String,
    pub input: u64,
    pub input_cached: u64,
    pub output: u64,
    pub reasoning: u64,
    pub total: u64,
    pub spend_usd: f64,
}

/// litellm proxy counters (remote providers), summed over its lifetime.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct RemoteTokens {
    pub status: String,
    pub models: Vec<RemoteModel>,
    pub total: u64,
    pub spend_usd: f64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct LocalModel {
    pub name: String,
    pub port: u16,
    pub input: u64,
    pub input_cached: u64,
    pub output: u64,
    pub total: u64,
}

/// llama.cpp counters (local models), summed since each server started.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct LocalTokens {
    pub status: String,
    pub models: Vec<LocalModel>,
    pub total: u64,
}

/// One model's share of the Pi transcripts. The model name is read from the
/// transcript record itself, so it needs no configuration here.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PiModel {
    pub name: String,
    pub provider: String,
    pub input: u64,
    pub output: u64,
    pub cache_read: u64,
    pub cache_write: u64,
    pub reasoning: u64,
    pub total: u64,
    pub cost_usd: f64,
    pub turns: u64,
}

/// Pi transcript totals. `cache_read` dominates in practice: it is the context
/// re-read on every turn, reported separately so the real prompt volume stays
/// visible. `models` splits the same turns per model; a local model reached
/// through Pi reports turns with zero tokens, which is real data, not a gap.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PiTokens {
    pub status: String,
    pub input: u64,
    pub output: u64,
    pub cache_read: u64,
    pub cache_write: u64,
    pub reasoning: u64,
    pub total: u64,
    pub cost_usd: f64,
    pub turns: u64,
    pub sessions: u64,
    pub models: Vec<PiModel>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct CodexWindow {
    pub label: String,
    pub used_percent: f64,
    pub window_minutes: u64,
    pub resets_at: String,
}

/// Codex subscription windows. Percentages, not token counts: the provider
/// reports consumption as a share of the window.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct CodexTokens {
    pub status: String,
    pub plan: String,
    pub windows: Vec<CodexWindow>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Tokens {
    pub remote: RemoteTokens,
    pub local: LocalTokens,
    pub pi: PiTokens,
    pub codex: CodexTokens,
}

/// Run the collector and parse its document. Errors are returned so the caller
/// can log them and keep the previous value instead of blanking the section.
pub fn collect_tokens() -> Result<Tokens, String> {
    let output = Command::new(TOKENS_BIN)
        .output()
        .map_err(|e| format!("pc-ai-tokens: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "pc-ai-tokens exited with {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    serde_json::from_slice(&output.stdout).map_err(|e| format!("pc-ai-tokens JSON parse error: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_document_round_trip() {
        let document = r#"{
            "remote": {
                "status": "ok",
                "total": 33043648,
                "spend_usd": 0.0,
                "models": [{
                    "name": "deepseek-v4-flash",
                    "input": 28199096,
                    "input_cached": 26620544,
                    "output": 145158,
                    "reasoning": 119445,
                    "total": 33043648,
                    "spend_usd": 0.0
                }]
            },
            "local": {
                "status": "ok",
                "total": 12,
                "models": [{
                    "name": "Abito-gpt", "port": 11002,
                    "input": 10, "input_cached": 4, "output": 2, "total": 12
                }]
            },
            "pi": {
                "status": "ok", "input": 117023070, "output": 7738392,
                "cache_read": 2468238288, "cache_write": 7401949,
                "reasoning": 3167501, "total": 2600401699,
                "cost_usd": 1998.9, "turns": 18267, "sessions": 93,
                "models": [
                    {
                        "name": "gpt-5.6-sol", "provider": "openai-codex",
                        "input": 104635790, "output": 6349798,
                        "cache_read": 2102997888, "cache_write": 0,
                        "reasoning": 3167501, "total": 2213983476,
                        "cost_usd": 1766.07, "turns": 15779
                    },
                    {
                        "name": "Abito-gpt", "provider": "abito-direct",
                        "input": 0, "output": 0, "cache_read": 0,
                        "cache_write": 0, "reasoning": 0, "total": 0,
                        "cost_usd": 0.0, "turns": 295
                    }
                ]
            },
            "codex": {
                "status": "ok", "plan": "prolite",
                "windows": [{
                    "label": "semanal", "used_percent": 74.0,
                    "window_minutes": 10080, "resets_at": "2026-09-22T06:35:20Z"
                }]
            }
        }"#;

        let tokens: Tokens = serde_json::from_str(document).expect("document must parse");

        assert_eq!(tokens.remote.total, 33043648);
        assert_eq!(tokens.remote.models[0].name, "deepseek-v4-flash");
        assert_eq!(tokens.local.models[0].port, 11002);
        assert_eq!(tokens.pi.cache_read, 2468238288);
        assert_eq!(tokens.pi.turns, 18267);
        assert_eq!(tokens.pi.models.len(), 2);
        assert_eq!(tokens.pi.models[0].name, "gpt-5.6-sol");
        assert_eq!(tokens.pi.models[0].provider, "openai-codex");
        assert_eq!(tokens.pi.models[0].total, 2213983476);
        // A local model reached through Pi keeps its turns with zero tokens.
        assert_eq!(tokens.pi.models[1].total, 0);
        assert_eq!(tokens.pi.models[1].turns, 295);
        assert_eq!(tokens.codex.windows[0].used_percent, 74.0);
    }

    #[test]
    fn test_unavailable_source_keeps_document_parseable() {
        // A section that failed only reports a status: missing fields must
        // default rather than reject the whole document.
        let document = r#"{
            "remote": {"status": "unavailable", "error": "remote: URLError"},
            "local": {"status": "ok", "total": 0, "models": []},
            "pi": {"status": "ok", "total": 0},
            "codex": {"status": "unavailable", "error": "codex: TimeoutExpired", "windows": []}
        }"#;

        let tokens: Tokens = serde_json::from_str(document).expect("document must parse");

        assert_eq!(tokens.remote.status, "unavailable");
        assert_eq!(tokens.remote.total, 0);
        assert!(tokens.remote.models.is_empty());
        assert!(tokens.pi.models.is_empty());
        assert_eq!(tokens.codex.status, "unavailable");
        assert!(tokens.codex.windows.is_empty());
    }
}
