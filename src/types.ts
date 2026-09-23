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
  gtt_gib: number;
  vram_gib: number;
}

export interface Group {
  rss_gib: number;
  cpu: number;
  pids: number[];
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

/// One model's share of the Pi transcripts. The name is read from the record
/// itself.
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

export interface Tokens {
  remote: RemoteTokens;
  local: LocalTokens;
  pi: PiTokens;
  codex: CodexTokens;
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
