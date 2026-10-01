// ---------------------------------------------------------------------------
// TypeScript types — mirror the Rust payloads exactly (snake_case).
// ---------------------------------------------------------------------------

export interface GetState {
  snapshot: FullSnapshot | null;
  history: HistorySample[];
}

export interface FullSnapshot {
  memory: Memory;
  models: Model[];
  groups: Record<string, Group>;
  processes: Process[];
  gpu: GpuMemory | null;
  ports: PortRow[];
  tokens: Tokens | null;
  /// Models installed on the machine (Ollama's catalog plus configured
  /// `model_dirs`), as opposed to the loaded `models` above.
  model_inventory: InstalledModel[];
}

/// GPU memory at machine level, read from `ioreg` on macOS. `null` when it
/// cannot be measured (the Linux collector does not report it yet).

export interface Memory {
  total_gib: number;
  used_gib: number;
  available_gib: number;
  swap_total_gib: number;
  swap_used_gib: number;
}

export interface GpuMemory {
  alloc_gib: number;
  in_use_gib: number;
}

export interface Model {
  alias: string;
  pid: number;
  port: number | null;
  model: string;
  rss_gib: number;
  cpu: number;
  /// GPU memory per model: `null` when it cannot be measured (macOS has no
  /// public API for it). A number, including 0, is a real measurement — the
  /// Linux collector always reports one.
  gtt_gib: number | null;
  vram_gib: number | null;
}

export interface Group {
  rss_gib: number;
  cpu: number;
  pids: number[];
}

/// One model installed on this machine (mirrors `InstalledModel` in
/// src-tauri/src/inventory.rs): present in Ollama's catalog or as a GGUF file
/// on disk, which is not the same as currently loaded/running. For disk
/// models, `parameters` and `quantization` are hints parsed from the file
/// name — a badly named file yields a wrong hint, never a measurement.
export interface InstalledModel {
  name: string;
  source: "ollama" | "disk" | string;
  size_bytes: number | null;
  parameters: string | null;
  quantization: string | null;
}

export interface Process {
  pid: number;
  ppid: number;
  uid: number;
  rss_kb: number;
  cpu: number;
  comm: string;
  args: string;
}

export interface PortRow {
  proto: string;
  state: string;
  local_address: string;
  peer_address: string;
  port: number;
  classification: string;
  service_name: string;
  process_name: string;
  pid: number;
}

export interface HistorySample {
  timestamp: string;
  used_gib: number;
  available_gib: number;
  group_rss_gib: number[]; // ordered: pi, hermes, firefox, system, other
}

// ---------------------------------------------------------------------------
// Token consumption (mirrors src-tauri/src/tokens.rs).
// ---------------------------------------------------------------------------

/// Every section carries its own status: one unavailable source degrades that
/// section only.
export type SourceStatus = "ok" | "unavailable" | string;

export interface RemoteModel {
  name: string;
  input: number;
  input_cached: number;
  output: number;
  reasoning: number;
  total: number;
  spend_usd: number;
}

/// litellm proxy counters: remote providers, summed over the proxy's lifetime.
export interface RemoteTokens {
  status: SourceStatus;
  models: RemoteModel[];
  total: number;
  spend_usd: number;
}

export interface LocalModel {
  name: string;
  port: number;
  input: number;
  input_cached: number;
  output: number;
  total: number;
}

/// llama.cpp counters: local models, summed since each server started.
export interface LocalTokens {
  status: SourceStatus;
  models: LocalModel[];
  total: number;
}

/// One model's share of a transcript source (pi, codex_cli, claude_code). The
/// name is read from the record itself.
export interface PiModel {
  name: string;
  provider: string;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  reasoning: number;
  total: number;
  cost_usd: number;
  turns: number;
}

/// Pi transcript totals. `cache_read` is the context re-read every turn, so it
/// is reported separately instead of inflating the prompt volume. `models`
/// splits the same turns per model; a local model reached through Pi reports
/// turns with zero tokens, which is real data rather than a gap.
export interface PiTokens {
  status: SourceStatus;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  reasoning: number;
  total: number;
  cost_usd: number;
  turns: number;
  sessions: number;
  models: PiModel[];
}

/// Codex subscription windows: percentages, not token counts.
export interface CodexWindow {
  label: string;
  used_percent: number;
  window_minutes: number;
  resets_at: string;
}

/// Codex subscription windows: percentages, not token counts.
export interface CodexTokens {
  status: SourceStatus;
  plan: string;
  windows: CodexWindow[];
}

/// Transcript totals for the CLI agents, mirroring Rust `TranscriptTokens`
/// (the same payload `pi` carries, `sessions` included). On macOS these are
/// the sections that actually have data: the native collectors scan the Codex
/// CLI rollouts and the Claude Code projects directly, with no Python needed.
export interface TranscriptTokens {
  status: SourceStatus;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  reasoning: number;
  total: number;
  cost_usd: number;
  turns: number;
  sessions: number;
  models: PiModel[];
}

export interface Tokens {
  remote: RemoteTokens;
  local: LocalTokens;
  pi: PiTokens;
  /// Codex CLI rollout transcripts, scanned natively from `~/.codex/sessions`.
  codex_cli: TranscriptTokens;
  /// Claude Code project transcripts, scanned natively from `~/.claude/projects`.
  claude_code: TranscriptTokens;
  codex: CodexTokens;
}

// ---------------------------------------------------------------------------
// Watch config (mirrors the daemon `GET`/`POST /api/config` payloads, i.e.
// `watch_json`/`watch_from_json` in src-tauri/src/config.rs). The JSON key is
// `match` even though the Rust side calls the field `patterns`.
// ---------------------------------------------------------------------------

/// One `[[watch]]` entry of `~/.config/macacoview/config.toml`: a process
/// group the collector builds. `match` holds regular expressions searched
/// against the process name (comm) and its full command line; an entry
/// without patterns is one of the collector's fixed rules (`system` catches
/// root processes, the other empty-pattern entry catches everything no
/// pattern claimed). `visible` decides whether the group is built at all.
export interface WatchEntry {
  name: string;
  match: string[];
  icon: string;
  visible: boolean;
}

/// The exact body of `GET /api/config` and of a successful `POST /api/config`
/// (the server answers with the config re-read from disk).
export interface WatchConfig {
  watch: WatchEntry[];
}

// ---------------------------------------------------------------------------
// Fixed group display order and labels (matches GTK refresh_resources).
// ---------------------------------------------------------------------------

export const GROUP_KEYS: readonly string[] = [
  "pi",
  "hermes",
  "firefox",
  "system",
  "other",
];

export const GROUP_LABELS: Record<string, string> = {
  pi: "🥧 Pi",
  hermes: "🪽 Hermes",
  firefox: "🦊 Firefox",
  system: "⚙️ Sistema (procesos root)",
  other: "📦 Otros procesos",
};
