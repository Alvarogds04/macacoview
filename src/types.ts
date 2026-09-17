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
  ports: PortRow[];
}

export interface Memory {
  total_gib: number;
  used_gib: number;
  available_gib: number;
  swap_total_gib: number;
  swap_used_gib: number;
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
