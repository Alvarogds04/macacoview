//! Token consumption per source.
//!
//! The HTTP-facing aggregation lives in `scripts/pc-ai-tokens` (installed as
//! `$HOME/.local/bin/pc-ai-tokens`), which talks to Prometheus, llama.cpp and
//! codexbar. This module runs it for those sources and deserializes the
//! document: keeping the HTTP sources out of the Rust tree avoids adding an
//! HTTP client to an app that needs none.
//!
//! The two transcript sources that only read local files, `codex_cli` and
//! `claude_code`, are filled by the native parsers at the bottom of this file,
//! not by the script: an incremental JSONL scan with a versioned per-source
//! cache, mirroring the Python engine. That is what lets macOS read the tokens
//! without spawning the script at all — when the script binary is missing, the
//! document still comes back, with the script-fed sections degraded to
//! `unavailable` and the two native sources fully populated. The parsers and
//! their tests compile and run on every platform — they only touch files, so
//! nothing is `cfg`-gated.
//!
//! Every section carries its own `status`, so one unavailable source degrades
//! that section instead of the whole snapshot. Missing fields default, which is
//! what makes a partially filled document still deserialize.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

fn tokens_bin() -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    format!("{home}/.local/bin/pc-ai-tokens")
}

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

/// One model's share of a transcript source. The model name is read from the
/// transcript record itself, so it needs no configuration here.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TranscriptModel {
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

/// Transcript totals, shared by the pi, codex_cli and claude_code sources.
/// `cache_read` dominates in practice: it is the context re-read on every turn,
/// reported separately so the real prompt volume stays visible. `models` splits
/// the same turns per model; a local model reached through Pi reports turns
/// with zero tokens, which is real data, not a gap.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TranscriptTokens {
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
    pub models: Vec<TranscriptModel>,
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
    pub pi: TranscriptTokens,
    /// Codex CLI rollout transcripts, scanned natively from `~/.codex/sessions`.
    pub codex_cli: TranscriptTokens,
    /// Claude Code project transcripts, scanned natively from `~/.claude/projects`.
    pub claude_code: TranscriptTokens,
    pub codex: CodexTokens,
}

/// Run the collector and parse its document. The script keeps feeding the
/// HTTP-facing sources (`remote`, `local`, `pi`, `codex`); the two transcript
/// sources (`codex_cli`, `claude_code`) are always scanned natively, over what
/// the script reported for them.
///
/// A missing script binary is not an error: macOS ships no Python, so the
/// document comes back with the script-fed sections marked `unavailable` and
/// the native sources populated. A script that runs but fails (non-zero exit,
/// unparsable output) is an error, so the caller keeps the previous value
/// instead of blanking a section that was showing real numbers.
pub fn collect_tokens() -> Result<Tokens, String> {
    collect_tokens_from(Path::new(&tokens_bin()), &home_dir())
}

/// The composition behind `collect_tokens`, parameterized so tests can run it
/// against a synthetic script path and a temporary home directory instead of
/// the real binary and the live transcripts.
fn collect_tokens_from(bin: &Path, home: &Path) -> Result<Tokens, String> {
    let mut tokens = run_script_document(bin)?;
    tokens.codex_cli = scan_transcripts(&codex_spec(home));
    tokens.claude_code = scan_transcripts(&claude_spec(home));
    Ok(tokens)
}

/// Run the script and parse its document, degrading to an all-`unavailable`
/// document only when the binary itself is absent.
fn run_script_document(bin: &Path) -> Result<Tokens, String> {
    let output = match Command::new(bin).output() {
        Ok(output) => output,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(script_unavailable_tokens());
        }
        Err(error) => return Err(format!("pc-ai-tokens: {error}")),
    };

    if !output.status.success() {
        return Err(format!(
            "pc-ai-tokens exited with {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    serde_json::from_slice(&output.stdout).map_err(|e| format!("pc-ai-tokens JSON parse error: {e}"))
}

/// The document used when the script binary does not exist: the sources only
/// the script can fill report `unavailable`, matching the script's own
/// per-source degradation vocabulary. The two native transcript sources are
/// left at their defaults here; `collect_tokens_from` overwrites them right
/// after with the real scans.
fn script_unavailable_tokens() -> Tokens {
    let mut tokens = Tokens::default();
    tokens.remote.status = "unavailable".to_string();
    tokens.local.status = "unavailable".to_string();
    tokens.pi.status = "unavailable".to_string();
    tokens.codex.status = "unavailable".to_string();
    tokens
}

/// One turn's token counters as a transcript line reports them. `total` is
/// whatever the source reports; Claude Code reports none and its parser
/// computes it as input + cache_read + cache_write + output.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Usage {
    pub input: f64,
    pub output: f64,
    pub cache_read: f64,
    pub cache_write: f64,
    pub reasoning: f64,
    pub total: f64,
    pub cost_usd: f64,
}

/// Running counters for one transcript source. The fields are `f64` to match
/// the Python collector's `as_float` cache format, so both implementations can
/// share one versioned cache file; the counters are whole numbers in practice.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct UsageSums {
    pub input: f64,
    pub output: f64,
    pub cache_read: f64,
    pub cache_write: f64,
    pub reasoning: f64,
    pub total: f64,
    pub cost_usd: f64,
    pub turns: f64,
}

impl UsageSums {
    fn merge(&mut self, other: &UsageSums) {
        self.input += other.input;
        self.output += other.output;
        self.cache_read += other.cache_read;
        self.cache_write += other.cache_write;
        self.reasoning += other.reasoning;
        self.total += other.total;
        self.cost_usd += other.cost_usd;
        self.turns += other.turns;
    }

    fn accumulate(&mut self, usage: &Usage) {
        self.merge(&UsageSums {
            input: usage.input,
            output: usage.output,
            cache_read: usage.cache_read,
            cache_write: usage.cache_write,
            reasoning: usage.reasoning,
            total: usage.total,
            cost_usd: usage.cost_usd,
            turns: 1.0,
        });
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
struct CachedModel {
    sums: UsageSums,
    provider: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
struct CachedFile {
    offset: u64,
    sums: UsageSums,
    models: BTreeMap<String, CachedModel>,
}

/// Cache document, shaped exactly like the Python collector's so both can
/// share the files under `~/.cache/pc-ai-tokens`. The version is bumped
/// whenever the shape changes: an older cache is ignored and the transcripts
/// are re-read from the start, which self-heals without manual cleanup.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
struct CacheDocument {
    version: u32,
    files: BTreeMap<String, CachedFile>,
}

/// What one raw JSONL line contributes. `usage` is `None` when the line
/// carries no token usage; a non-empty `model` re-points the model later usage
/// lines are attributed to (Codex announces the model in a separate record).
pub struct ParsedLine {
    usage: Option<Usage>,
    model: Option<String>,
    provider: String,
}

impl ParsedLine {
    fn none() -> Self {
        Self {
            usage: None,
            model: None,
            provider: String::new(),
        }
    }
}

/// Cache versions, mirroring the Python collector's `CODEX_CLI_CACHE_VERSION`
/// and `CLAUDE_CACHE_VERSION`.
pub const CODEX_CLI_CACHE_VERSION: u32 = 1;
pub const CLAUDE_CACHE_VERSION: u32 = 1;

fn home_dir() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default())
}

/// Incremental scan parameters for one transcript source.
struct SourceSpec {
    root: PathBuf,
    cache_path: PathBuf,
    cache_version: u32,
    /// File-name glob: either `*.jsonl` or a prefix glob like `rollout-*.jsonl`.
    pattern: &'static str,
    parse_line: fn(&[u8]) -> ParsedLine,
}

/// Codex CLI rollouts (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`), read
/// incrementally with the same cache files the Python collector uses.
fn codex_spec(home: &Path) -> SourceSpec {
    SourceSpec {
        root: home.join(".codex").join("sessions"),
        cache_path: home
            .join(".cache")
            .join("pc-ai-tokens")
            .join("codex-cli-sessions.json"),
        cache_version: CODEX_CLI_CACHE_VERSION,
        pattern: "rollout-*.jsonl",
        parse_line: codex_parse_line,
    }
}

/// Claude Code project transcripts (`~/.claude/projects/*/*.jsonl`), read
/// incrementally with the same cache files the Python collector uses.
fn claude_spec(home: &Path) -> SourceSpec {
    SourceSpec {
        root: home.join(".claude").join("projects"),
        cache_path: home
            .join(".cache")
            .join("pc-ai-tokens")
            .join("claude-code-sessions.json"),
        cache_version: CLAUDE_CACHE_VERSION,
        pattern: "*.jsonl",
        parse_line: claude_parse_line,
    }
}

/// Incrementally scans JSONL transcripts under `spec.root`.
///
/// The cache keeps the byte offset already consumed per file, so a poll parses
/// only the bytes appended since the last one; a file that shrank is re-read
/// from the start, which covers truncation and rotation. A malformed line is
/// skipped, never fatal for the rest of the file. Transcripts that no longer
/// exist are dropped from the cache so a deleted session stops counting.
fn scan_transcripts(spec: &SourceSpec) -> TranscriptTokens {
    let stored = load_cache(&spec.cache_path, spec.cache_version);
    let mut totals = UsageSums::default();
    let mut per_model: BTreeMap<String, CachedModel> = BTreeMap::new();
    let mut visited: BTreeMap<String, CachedFile> = BTreeMap::new();
    let mut sessions = 0u64;

    for path in transcript_files(&spec.root, spec.pattern) {
        let key = path.to_string_lossy().into_owned();
        let size = match std::fs::metadata(&path) {
            Ok(meta) => meta.len(),
            Err(_) => continue,
        };
        let mut state = stored.get(&key).cloned().unwrap_or_default();
        if state.offset > size {
            // Truncated or rotated: start over.
            state = CachedFile::default();
        }

        let mut current_model = String::from("sin-modelo");
        if size > state.offset {
            if let Some(appended) = read_appended(&path, state.offset, size) {
                // `split` always yields a final chunk: empty when the region
                // ends with a newline, the partial line when it does not. The
                // partial stays unparsed and unconsumed until it completes.
                let chunks: Vec<&[u8]> = appended.split(|&byte| byte == b'\n').collect();
                let mut consumed = 0u64;
                for line in &chunks[..chunks.len() - 1] {
                    consumed += line.len() as u64 + 1;
                    let parsed = (spec.parse_line)(line);
                    if let Some(model) = parsed.model {
                        current_model = model;
                    }
                    let Some(usage) = parsed.usage else {
                        continue;
                    };
                    state.sums.accumulate(&usage);
                    let entry = state.models.entry(current_model.clone()).or_default();
                    entry.sums.accumulate(&usage);
                    if !parsed.provider.is_empty() {
                        entry.provider = parsed.provider.clone();
                    }
                }
                state.offset += consumed;
            }
        }

        if state.sums.turns > 0.0 {
            sessions += 1;
        }
        totals.merge(&state.sums);
        for (name, entry) in &state.models {
            let acc = per_model.entry(name.clone()).or_default();
            acc.sums.merge(&entry.sums);
            if !entry.provider.is_empty() {
                acc.provider = entry.provider.clone();
            }
        }
        visited.insert(key, state);
    }

    store_cache(&spec.cache_path, &visited, spec.cache_version);
    transcript_payload(totals, per_model, sessions)
}

fn load_cache(path: &Path, version: u32) -> BTreeMap<String, CachedFile> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return BTreeMap::new();
    };
    let Ok(document) = serde_json::from_str::<CacheDocument>(&text) else {
        return BTreeMap::new();
    };
    if document.version != version {
        return BTreeMap::new();
    }
    document.files
}

fn store_cache(path: &Path, files: &BTreeMap<String, CachedFile>, version: u32) {
    let document = CacheDocument {
        version,
        files: files.clone(),
    };
    if let Some(parent) = path.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return;
        }
    }
    if let Ok(text) = serde_json::to_string(&document) {
        let _ = std::fs::write(path, text);
    }
}

fn read_appended(path: &Path, offset: u64, size: u64) -> Option<Vec<u8>> {
    use std::io::{Read, Seek, SeekFrom};
    let mut handle = std::fs::File::open(path).ok()?;
    handle.seek(SeekFrom::Start(offset)).ok()?;
    let mut appended = Vec::new();
    handle.take(size - offset).read_to_end(&mut appended).ok()?;
    Some(appended)
}

fn transcript_files(root: &Path, pattern: &str) -> Vec<PathBuf> {
    let mut files = Vec::new();
    if root.is_dir() {
        collect_matching(root, pattern, 0, &mut files);
    }
    files.sort();
    files
}

fn collect_matching(dir: &Path, pattern: &str, depth: u8, out: &mut Vec<PathBuf>) {
    // `sessions/YYYY/MM/DD` is the deepest real layout; the cap also stops any
    // pathological tree from growing the walk unbounded.
    if depth > 6 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        let path = entry.path();
        // Symlinked directories are skipped: `file_type` does not follow them,
        // which keeps a link loop from hanging the scan.
        if file_type.is_dir() {
            collect_matching(&path, pattern, depth + 1, out);
        } else if file_type.is_file() && file_name_matches(path.file_name(), pattern) {
            out.push(path);
        }
    }
}

/// Minimal glob for `*.jsonl`-style patterns: an optional literal prefix
/// before the `*` and a literal suffix after it.
fn file_name_matches(name: Option<&std::ffi::OsStr>, pattern: &str) -> bool {
    let Some(name) = name.and_then(|name| name.to_str()) else {
        return false;
    };
    let Some((prefix, suffix)) = pattern.split_once('*') else {
        return name == pattern;
    };
    name.starts_with(prefix) && name.ends_with(suffix)
}

fn has_subslice(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|window| window == needle)
}

fn num(value: &serde_json::Value, key: &str) -> f64 {
    value
        .get(key)
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(0.0)
}

fn object_of(value: &serde_json::Value) -> Option<&serde_json::Value> {
    if value.is_object() { Some(value) } else { None }
}

/// Codex CLI rollout line. The model is announced once per turn by a
/// `turn_context` record — whose `payload.type` is null there — and the useful
/// identifier sits in `payload.model`. `total_token_usage` is cumulative per
/// thread and resets to zero when a resumed thread is appended to the same
/// rollout file, so only the `last_token_usage` per-turn delta is ever summed:
/// summing the cumulative total silently undercounts resumed threads.
pub fn codex_parse_line(raw: &[u8]) -> ParsedLine {
    if !has_subslice(raw, b"\"token_count\"") && !has_subslice(raw, b"\"turn_context\"") {
        return ParsedLine::none();
    }
    let Ok(record) = serde_json::from_slice::<serde_json::Value>(raw) else {
        return ParsedLine::none();
    };
    let Some(payload) = record.get("payload").and_then(object_of) else {
        return ParsedLine::none();
    };
    if record.get("type").and_then(serde_json::Value::as_str) == Some("turn_context") {
        return ParsedLine {
            usage: None,
            model: payload
                .get("model")
                .and_then(serde_json::Value::as_str)
                .filter(|model| !model.is_empty())
                .map(str::to_string),
            provider: String::new(),
        };
    }
    if payload.get("type").and_then(serde_json::Value::as_str) != Some("token_count") {
        return ParsedLine::none();
    }
    let Some(last) = payload
        .get("info")
        .and_then(object_of)
        .and_then(|info| info.get("last_token_usage"))
        .and_then(object_of)
    else {
        return ParsedLine::none();
    };
    ParsedLine {
        usage: Some(Usage {
            input: num(last, "input_tokens"),
            output: num(last, "output_tokens"),
            cache_read: num(last, "cached_input_tokens"),
            cache_write: num(last, "cache_write_input_tokens"),
            reasoning: num(last, "reasoning_output_tokens"),
            total: num(last, "total_tokens"),
            cost_usd: 0.0,
        }),
        model: None,
        provider: String::new(),
    }
}

/// Claude Code project line. The API reports no total, so it is computed as
/// the sum of every billed component: input + cache_read + cache_write +
/// output. The snake_case keys never appear in the Pi transcripts (camelCase),
/// so this parser autodetects only its own schema. Every usage-bearing entry
/// counts as one turn, matching the reference collector: Claude Code repeats
/// the same message id across entries, and deduplicating would undercount.
pub fn claude_parse_line(raw: &[u8]) -> ParsedLine {
    if !has_subslice(raw, b"\"usage\"") {
        return ParsedLine::none();
    }
    let Ok(record) = serde_json::from_slice::<serde_json::Value>(raw) else {
        return ParsedLine::none();
    };
    let Some(message) = record.get("message").and_then(object_of) else {
        return ParsedLine::none();
    };
    let Some(usage) = message.get("usage").and_then(object_of) else {
        return ParsedLine::none();
    };
    if usage.get("input_tokens").is_none() && usage.get("output_tokens").is_none() {
        return ParsedLine::none();
    }
    let input = num(usage, "input_tokens");
    let cache_read = num(usage, "cache_read_input_tokens");
    let cache_write = num(usage, "cache_creation_input_tokens");
    let output = num(usage, "output_tokens");
    let reasoning = usage
        .get("output_tokens_details")
        .and_then(|details| details.get("thinking_tokens"))
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(0.0);
    ParsedLine {
        usage: Some(Usage {
            input,
            output,
            cache_read,
            cache_write,
            reasoning,
            total: input + cache_read + cache_write + output,
            cost_usd: 0.0,
        }),
        model: Some(
            message
                .get("model")
                .and_then(serde_json::Value::as_str)
                .filter(|model| !model.is_empty())
                .unwrap_or("sin-modelo")
                .to_string(),
        ),
        provider: String::new(),
    }
}

fn round6(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn transcript_payload(
    totals: UsageSums,
    per_model: BTreeMap<String, CachedModel>,
    sessions: u64,
) -> TranscriptTokens {
    let mut models: Vec<TranscriptModel> = per_model
        .into_iter()
        .filter(|(_, entry)| entry.sums.turns > 0.0)
        .map(|(name, entry)| TranscriptModel {
            name,
            provider: entry.provider,
            input: entry.sums.input as u64,
            output: entry.sums.output as u64,
            cache_read: entry.sums.cache_read as u64,
            cache_write: entry.sums.cache_write as u64,
            reasoning: entry.sums.reasoning as u64,
            total: entry.sums.total as u64,
            cost_usd: round6(entry.sums.cost_usd),
            turns: entry.sums.turns as u64,
        })
        .collect();
    // Zero-token rows are real and stay: a model that reports turns but no
    // usage counters ranks by turns after the models that do report tokens.
    models.sort_by(|a, b| b.total.cmp(&a.total).then(b.turns.cmp(&a.turns)));

    TranscriptTokens {
        status: "ok".to_string(),
        input: totals.input as u64,
        output: totals.output as u64,
        cache_read: totals.cache_read as u64,
        cache_write: totals.cache_write as u64,
        reasoning: totals.reasoning as u64,
        total: totals.total as u64,
        cost_usd: round6(totals.cost_usd),
        turns: totals.turns as u64,
        sessions,
        models,
    }
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

    #[test]
    fn test_transcript_sections_round_trip_and_default() {
        let document = r#"{
            "codex_cli": {
                "status": "ok", "input": 100, "output": 5,
                "cache_read": 80, "cache_write": 0, "reasoning": 2,
                "total": 105, "cost_usd": 0.0, "turns": 2, "sessions": 1,
                "models": [{
                    "name": "gpt-5.6-sol", "provider": "",
                    "input": 100, "output": 5, "cache_read": 80,
                    "cache_write": 0, "reasoning": 2, "total": 105,
                    "cost_usd": 0.0, "turns": 2
                }]
            },
            "claude_code": {"status": "ok", "total": 7, "turns": 1, "sessions": 1}
        }"#;

        let tokens: Tokens = serde_json::from_str(document).expect("document must parse");

        assert_eq!(tokens.codex_cli.total, 105);
        assert_eq!(tokens.codex_cli.models[0].name, "gpt-5.6-sol");
        assert_eq!(tokens.claude_code.total, 7);

        // Older documents without the new sections keep parsing.
        let legacy: Tokens = serde_json::from_str(r#"{"pi": {"total": 9}}"#).expect("must parse");
        assert_eq!(legacy.pi.total, 9);
        assert_eq!(legacy.codex_cli.total, 0);
        assert!(legacy.claude_code.models.is_empty());
    }
}

#[cfg(test)]
mod transcript_tests {
    use super::*;

    // Synthetic fixtures only: the real transcripts contain this machine's
    // paths and must never be copied into the repo. The ground-truth check
    // against them is the `#[ignore]`d test at the bottom of this module.

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "pc-ai-tokens-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .subsec_nanos()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn spec(root: &Path, cache: &Path, pattern: &'static str, parser: fn(&[u8]) -> ParsedLine) -> SourceSpec {
        SourceSpec {
            root: root.to_path_buf(),
            cache_path: cache.to_path_buf(),
            cache_version: 1,
            pattern,
            parse_line: parser,
        }
    }

    fn write_file(path: &Path, contents: &[u8]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, contents).unwrap();
    }

    fn codex_turn_context(model: &str) -> String {
        format!(
            r#"{{"type":"turn_context","payload":{{"turn_id":"t","cwd":"/tmp","model":"{model}","effort":"high"}}}}"#
        )
    }

    fn codex_event(last: u64, cumulative: u64) -> String {
        format!(
            r#"{{"type":"event_msg","payload":{{"type":"token_count","info":{{"total_token_usage":{{"input_tokens":{cumulative},"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":0,"reasoning_output_tokens":0,"total_tokens":{cumulative}}},"last_token_usage":{{"input_tokens":{last},"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":0,"reasoning_output_tokens":0,"total_tokens":{last}}}}}}}}}"#
        )
    }

    fn claude_line(input: u64, output: u64) -> String {
        format!(
            r#"{{"type":"assistant","message":{{"id":"msg_1","model":"claude-sonnet-5","usage":{{"input_tokens":{input},"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":{output},"output_tokens_details":{{"thinking_tokens":1}}}}}}}}"#
        )
    }

    // -- codex parser ---------------------------------------------------------

    #[test]
    fn codex_sums_the_last_token_usage_delta_not_the_cumulative_total() {
        let line = format!(
            r#"{{"type":"event_msg","payload":{{"type":"token_count","info":{{"total_token_usage":{{"input_tokens":1000,"cached_input_tokens":800,"cache_write_input_tokens":0,"output_tokens":100,"reasoning_output_tokens":30,"total_tokens":1100}},"last_token_usage":{{"input_tokens":300,"cached_input_tokens":200,"cache_write_input_tokens":0,"output_tokens":40,"reasoning_output_tokens":10,"total_tokens":340}},"model_context_window":258400}}}}}}"#
        );
        let parsed = codex_parse_line(line.as_bytes());
        let usage = parsed.usage.unwrap();
        // The cumulative block says 1000; the per-turn delta is 300. Summing
        // the cumulative total undercounts resumed threads, so only the delta
        // is ever added.
        assert_eq!(usage.input, 300.0);
        assert_eq!(usage.cache_read, 200.0);
        assert_eq!(usage.output, 40.0);
        assert_eq!(usage.reasoning, 10.0);
        assert_eq!(usage.total, 340.0);
    }

    #[test]
    fn codex_event_without_info_is_skipped() {
        let line = r#"{"type":"event_msg","payload":{"type":"token_count","info":null}}"#;
        assert!(codex_parse_line(line.as_bytes()).usage.is_none());
    }

    #[test]
    fn codex_turn_context_points_the_model_and_carries_no_usage() {
        // Verified against a real rollout: payload.type is null here and the
        // model sits in payload.model.
        let line = codex_turn_context("gpt-5.6-sol");
        let parsed = codex_parse_line(line.as_bytes());
        assert!(parsed.usage.is_none());
        assert_eq!(parsed.model.as_deref(), Some("gpt-5.6-sol"));
    }

    #[test]
    fn codex_ignores_foreign_schemas_and_garbage() {
        // A Claude Code record is not a Codex record.
        let claude = claude_line(10, 5);
        assert!(codex_parse_line(claude.as_bytes()).usage.is_none());
        // A malformed line is skipped, never fatal.
        assert!(codex_parse_line(b"{not json").usage.is_none());
        assert!(codex_parse_line(b"").usage.is_none());
    }

    // -- claude parser --------------------------------------------------------

    #[test]
    fn claude_computes_the_total_the_api_does_not_report() {
        let line = r#"{"type":"assistant","message":{"id":"msg_1","model":"claude-sonnet-5","usage":{"input_tokens":4,"cache_creation_input_tokens":90,"cache_read_input_tokens":1300,"output_tokens":12,"output_tokens_details":{"thinking_tokens":7}}}}"#;
        let parsed = claude_parse_line(line.as_bytes());
        let usage = parsed.usage.unwrap();
        assert_eq!(usage.input, 4.0);
        assert_eq!(usage.cache_read, 1300.0);
        assert_eq!(usage.cache_write, 90.0);
        assert_eq!(usage.output, 12.0);
        assert_eq!(usage.reasoning, 7.0);
        // input + cache_read + cache_write + output.
        assert_eq!(usage.total, 1406.0);
        assert_eq!(parsed.model.as_deref(), Some("claude-sonnet-5"));
    }

    #[test]
    fn claude_ignores_the_pi_camelcase_schema_and_garbage() {
        // Pi transcripts use camelCase usage keys; Claude Code's snake_case
        // autodetection must never claim them.
        let pi_line = r#"{"message":{"model":"m","provider":"p","usage":{"input":1,"output":2,"cacheRead":3,"cacheWrite":4,"totalTokens":10}}}"#;
        assert!(claude_parse_line(pi_line.as_bytes()).usage.is_none());
        assert!(claude_parse_line(b"nope").usage.is_none());
    }

    #[test]
    fn claude_defaults_thinking_to_zero_without_details() {
        let line = r#"{"message":{"model":"claude-sonnet-5","usage":{"input_tokens":4,"output_tokens":2}}}"#;
        let usage = claude_parse_line(line.as_bytes()).usage.unwrap();
        assert_eq!(usage.reasoning, 0.0);
        assert_eq!(usage.total, 6.0);
    }

    #[test]
    fn claude_counts_every_usage_entry_one_turn_per_reference() {
        // Claude Code repeats the same message id across entries of one turn.
        // The reference collector counts each usage-bearing entry (the 25 real
        // entries include those repeats), so deduplicating would undercount.
        let file = format!("{}\n{}\n", claude_line(4, 2), claude_line(4, 2));
        let dir = temp_dir("claude-dedup");
        let root = dir.join("projects/-home-test");
        write_file(&root.join("session.jsonl"), file.as_bytes());
        let tokens = scan_transcripts(&spec(
            &root,
            &dir.join("cache.json"),
            "*.jsonl",
            claude_parse_line,
        ));
        assert_eq!(tokens.turns, 2);
        assert_eq!(tokens.total, 12);
    }

    // -- incremental scan engine ----------------------------------------------

    #[test]
    fn codex_thread_restart_survives_because_only_deltas_are_summed() {
        // One rollout file, a thread that accumulates 100 + 50, then a resumed
        // thread whose cumulative counter restarts at 70. Summing deltas gives
        // 220; naively summing total_token_usage would give 320.
        let dir = temp_dir("codex-restart");
        let root = dir.join("sessions/2026/09/01");
        let file = format!(
            "{}\n{}\n{}\n{}\n",
            codex_turn_context("gpt-5.6-sol"),
            codex_event(100, 100),
            codex_event(50, 150),
            codex_event(70, 70)
        );
        write_file(&root.join("rollout-a.jsonl"), file.as_bytes());
        let tokens = scan_transcripts(&spec(
            &root,
            &dir.join("cache.json"),
            "rollout-*.jsonl",
            codex_parse_line,
        ));
        assert_eq!(tokens.total, 220);
        assert_eq!(tokens.turns, 3);
        assert_eq!(tokens.sessions, 1);
        assert_eq!(tokens.models[0].name, "gpt-5.6-sol");
        assert_eq!(tokens.models[0].total, 220);
    }

    #[test]
    fn codex_attributes_usage_to_the_last_announced_model() {
        let dir = temp_dir("codex-models");
        let root = dir.join("sessions/2026/09/01");
        let file = format!(
            "{}\n{}\n{}\n{}\n",
            codex_turn_context("gpt-a"),
            codex_event(100, 100),
            codex_turn_context("gpt-b"),
            codex_event(10, 10)
        );
        write_file(&root.join("rollout-a.jsonl"), file.as_bytes());
        let tokens = scan_transcripts(&spec(
            &root,
            &dir.join("cache.json"),
            "rollout-*.jsonl",
            codex_parse_line,
        ));
        assert_eq!(tokens.models.len(), 2);
        assert_eq!(tokens.models[0].name, "gpt-a");
        assert_eq!(tokens.models[0].total, 100);
        assert_eq!(tokens.models[1].name, "gpt-b");
        assert_eq!(tokens.models[1].total, 10);
    }

    #[test]
    fn scan_is_incremental_and_keeps_partial_lines_unparsed() {
        let dir = temp_dir("scan-incremental");
        let root = dir.join("sessions/2026/09/01");
        let rollout = root.join("rollout-a.jsonl");
        let cache = dir.join("cache.json");
        let part1 = format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(100, 100));
        write_file(&rollout, part1.as_bytes());

        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.total, 100);
        let cached: CacheDocument =
            serde_json::from_str(&std::fs::read_to_string(&cache).unwrap()).unwrap();
        assert_eq!(cached.files[rollout.to_str().unwrap()].offset, part1.len() as u64);

        // A complete line appended without its trailing newline stays
        // unparsed and unconsumed.
        let partial = codex_event(50, 50);
        std::fs::write(&rollout, format!("{part1}{partial}").as_bytes()).unwrap();
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.total, 100, "partial line stays out of the totals");
        let cached: CacheDocument =
            serde_json::from_str(&std::fs::read_to_string(&cache).unwrap()).unwrap();
        assert_eq!(cached.files[rollout.to_str().unwrap()].offset, part1.len() as u64);

        // Once the newline arrives, only the appended bytes are parsed.
        std::fs::write(&rollout, format!("{part1}{partial}\n").as_bytes()).unwrap();
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.total, 150);
        assert_eq!(tokens.turns, 2);
    }

    #[test]
    fn scan_rereads_from_scratch_when_the_file_shrank() {
        let dir = temp_dir("scan-truncated");
        let root = dir.join("sessions/2026/09/01");
        let rollout = root.join("rollout-a.jsonl");
        let file = format!(
            "{}\n{}\n{}\n",
            codex_turn_context("gpt-a"),
            codex_event(100, 100),
            codex_event(50, 150)
        );
        write_file(&rollout, file.as_bytes());
        let cache = dir.join("cache.json");
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.total, 150);

        // Truncation or rotation: the offset now sits past the file end, so
        // the whole file is re-read and the old counters are discarded.
        let rotated = format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(70, 70));
        std::fs::write(&rollout, rotated.as_bytes()).unwrap();
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.total, 70);
        assert_eq!(tokens.turns, 1);
    }

    #[test]
    fn scan_forgets_deleted_transcripts() {
        let dir = temp_dir("scan-prune");
        let root = dir.join("sessions/2026/09/01");
        let first = format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(100, 100));
        let second = format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(10, 10));
        write_file(&root.join("rollout-a.jsonl"), first.as_bytes());
        write_file(&root.join("rollout-b.jsonl"), second.as_bytes());
        let cache = dir.join("cache.json");
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.turns, 2);
        assert_eq!(tokens.sessions, 2);

        std::fs::remove_file(&root.join("rollout-b.jsonl")).unwrap();
        let tokens = scan_transcripts(&spec(&root, &cache, "rollout-*.jsonl", codex_parse_line));
        assert_eq!(tokens.turns, 1, "a deleted session stops counting");
        assert_eq!(tokens.sessions, 1);
        let cached: CacheDocument =
            serde_json::from_str(&std::fs::read_to_string(&cache).unwrap()).unwrap();
        assert_eq!(cached.files.len(), 1);
    }

    #[test]
    fn one_malformed_line_never_stops_the_scan() {
        let dir = temp_dir("scan-malformed");
        let root = dir.join("projects/-home-test");
        let file = format!(
            "{}\nnot json at all\n{{broken\n{}\n",
            claude_line(4, 2),
            claude_line(6, 3)
        );
        write_file(&root.join("session.jsonl"), file.as_bytes());
        let tokens = scan_transcripts(&spec(&root, &dir.join("cache.json"), "*.jsonl", claude_parse_line));
        assert_eq!(tokens.turns, 2);
        assert_eq!(tokens.total, 15);
    }

    #[test]
    fn sources_autodetect_only_their_own_schema() {
        // Each parser rejects the other's records, so one source scanning a
        // shared directory cannot claim the other's lines.
        let dir = temp_dir("schema-split");
        let codex_root = dir.join("codex");
        let claude_root = dir.join("claude");
        let codex_file = format!(
            "{}\n{}\n{}\n",
            codex_turn_context("gpt-a"),
            claude_line(4, 2),
            codex_event(100, 100)
        );
        let claude_file = format!(
            "{}\n{}\n",
            codex_turn_context("gpt-a"),
            claude_line(4, 2)
        );
        write_file(&codex_root.join("rollout-a.jsonl"), codex_file.as_bytes());
        write_file(&claude_root.join("session.jsonl"), claude_file.as_bytes());

        let codex = scan_transcripts(&spec(&codex_root, &dir.join("c1.json"), "rollout-*.jsonl", codex_parse_line));
        let claude = scan_transcripts(&spec(&claude_root, &dir.join("c2.json"), "*.jsonl", claude_parse_line));

        assert_eq!(codex.total, 100);
        assert_eq!(codex.turns, 1);
        assert_eq!(claude.total, 6);
        assert_eq!(claude.turns, 1);
        assert_eq!(claude.models[0].name, "claude-sonnet-5");
    }

    #[test]
    fn missing_root_reports_an_empty_ok_source() {
        let dir = temp_dir("scan-empty");
        let tokens = scan_transcripts(&spec(
            &dir.join("does-not-exist"),
            &dir.join("cache.json"),
            "*.jsonl",
            claude_parse_line,
        ));
        assert_eq!(tokens.status, "ok");
        assert_eq!(tokens.total, 0);
        assert_eq!(tokens.turns, 0);
        assert!(tokens.models.is_empty());
    }

    // -- integration with the collector document ------------------------------

    #[cfg(unix)]
    fn write_executable_script(path: &Path, body: &str) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(path, body).unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn missing_script_degrades_script_sources_and_keeps_the_native_ones() {
        // macOS has no Python, so the script binary is absent there: the
        // document must still come back, with the script-fed sections marked
        // unavailable and the two native sources filled from the transcripts.
        let home = temp_dir("collect-no-script");
        let codex_file = format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(100, 100));
        write_file(
            &home.join(".codex/sessions/2026/09/01/rollout-a.jsonl"),
            codex_file.as_bytes(),
        );
        write_file(
            &home.join(".claude/projects/-home-test/session.jsonl"),
            format!("{}\n", claude_line(4, 2)).as_bytes(),
        );

        let tokens =
            collect_tokens_from(Path::new("/nonexistent/pc-ai-tokens"), &home).expect("must not fail");

        assert_eq!(tokens.remote.status, "unavailable");
        assert_eq!(tokens.local.status, "unavailable");
        assert_eq!(tokens.pi.status, "unavailable");
        assert_eq!(tokens.codex.status, "unavailable");
        assert_eq!(tokens.codex_cli.status, "ok");
        assert_eq!(tokens.codex_cli.total, 100);
        assert_eq!(tokens.codex_cli.models[0].name, "gpt-a");
        assert_eq!(tokens.claude_code.status, "ok");
        assert_eq!(tokens.claude_code.total, 6);
    }

    #[cfg(unix)]
    #[test]
    fn script_sources_survive_and_native_sources_own_their_sections() {
        // The script also reports codex_cli/claude_code on machines where it
        // runs; the native scans always win those two sections, while the
        // script's own sources pass through untouched.
        let dir = temp_dir("collect-with-script");
        let home = dir.join("home");
        let script = dir.join("fake-pc-ai-tokens");
        write_executable_script(
            &script,
            r#"#!/bin/sh
echo '{"remote":{"status":"ok","total":77},"local":{"status":"ok","total":3},"pi":{"status":"ok","total":9},"codex_cli":{"status":"ok","total":1},"claude_code":{"status":"ok","total":2},"codex":{"status":"ok","plan":"prolite"}}'
"#,
        );
        write_file(
            &home.join(".codex/sessions/2026/09/01/rollout-a.jsonl"),
            format!("{}\n{}\n", codex_turn_context("gpt-a"), codex_event(100, 100)).as_bytes(),
        );
        write_file(
            &home.join(".claude/projects/-home-test/session.jsonl"),
            format!("{}\n", claude_line(4, 2)).as_bytes(),
        );

        let tokens = collect_tokens_from(&script, &home).expect("script must succeed");

        assert_eq!(tokens.remote.total, 77);
        assert_eq!(tokens.local.total, 3);
        assert_eq!(tokens.pi.total, 9);
        assert_eq!(tokens.codex.plan, "prolite");
        // Overridden by the native scans, not the script's 1 and 2.
        assert_eq!(tokens.codex_cli.total, 100);
        assert_eq!(tokens.claude_code.total, 6);
    }

    #[cfg(unix)]
    #[test]
    fn a_script_that_runs_and_fails_is_still_an_error() {
        // Only a missing binary degrades the document; a script that runs and
        // exits non-zero keeps the caller's keep-previous-value behavior.
        let dir = temp_dir("collect-failing");
        let script = dir.join("failing-pc-ai-tokens");
        write_executable_script(&script, "#!/bin/sh\necho boom >&2\nexit 3\n");

        let error = collect_tokens_from(&script, &dir.join("home")).unwrap_err();
        assert!(error.contains("exited with"), "unexpected error: {error}");
    }

    /// Ground-truth check against the live transcripts of this machine. It
    /// never runs by default: the real transcripts must be read in place, not
    /// copied into the repo. Run explicitly with
    /// `cargo test --lib -- --ignored real_transcripts --nocapture`.
    #[test]
    #[ignore = "reads live ~/.codex and ~/.claude transcripts; run explicitly"]
    fn real_transcripts_match_the_python_collector() {
        let codex = scan_transcripts(&codex_spec(&home_dir()));
        let claude = scan_transcripts(&claude_spec(&home_dir()));
        eprintln!(
            "codex_cli: total={} turns={} sessions={}",
            codex.total, codex.turns, codex.sessions
        );
        for model in &codex.models {
            eprintln!("  {}: total={} turns={}", model.name, model.total, model.turns);
        }
        eprintln!(
            "claude_code: total={} turns={} sessions={}",
            claude.total, claude.turns, claude.sessions
        );
        for model in &claude.models {
            eprintln!("  {}: total={} turns={}", model.name, model.total, model.turns);
        }
    }
}
