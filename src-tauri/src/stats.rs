use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Memory {
    pub total_gib: f64,
    pub used_gib: f64,
    pub available_gib: f64,
    pub swap_total_gib: f64,
    pub swap_used_gib: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Model {
    pub alias: String,
    pub pid: i64,
    pub port: Option<i64>,
    pub model: String,
    pub rss_gib: f64,
    pub cpu: f64,
    pub gtt_gib: f64,
    pub vram_gib: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Group {
    pub rss_gib: f64,
    pub cpu: f64,
    pub pids: Vec<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Process {
    pub pid: i64,
    pub ppid: i64,
    pub uid: i64,
    pub rss_kb: i64,
    pub cpu: f64,
    pub comm: String,
    pub args: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stats {
    pub memory: Memory,
    pub models: Vec<Model>,
    pub groups: Groups,
    pub processes: Vec<Process>,
}

// Groups is a map of named groups
pub type Groups = std::collections::HashMap<String, Group>;
