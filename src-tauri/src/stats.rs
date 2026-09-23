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

/// GPU memory at machine level, measured on macOS from the IOAccelerator
/// counters in `ioreg`. The Linux collector (Python script) does not report
/// it yet, so `Stats::gpu` is `#[serde(default)]` and stays `None` there;
/// on macOS `collect_stats()` fills it.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct GpuMemory {
    pub alloc_gib: f64,
    pub in_use_gib: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stats {
    pub memory: Memory,
    pub models: Vec<Model>,
    pub groups: Groups,
    pub processes: Vec<Process>,
    #[serde(default)]
    pub gpu: Option<GpuMemory>,
}

// Groups is a map of named groups
pub type Groups = std::collections::HashMap<String, Group>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_ausente_en_el_json_de_linux_no_rompe_la_deserializacion() {
        // El colector de Linux (script Python) no reporta GPU: el campo falta
        // por completo en su documento JSON y no debe romper nada.
        let json = r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
            "swap_total_gib": 0.0, "swap_used_gib": 0.0},
            "models": [], "groups": {}, "processes": []}"#;
        let stats: Stats = serde_json::from_str(json).unwrap();
        assert_eq!(stats.gpu, None);

        // En macOS collect_stats() si lo llena.
        let con_gpu = r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
            "swap_total_gib": 0.0, "swap_used_gib": 0.0},
            "models": [], "groups": {}, "processes": [],
            "gpu": {"alloc_gib": 0.25, "in_use_gib": 0.125}}"#;
        let stats: Stats = serde_json::from_str(con_gpu).unwrap();
        assert_eq!(stats.gpu, Some(GpuMemory { alloc_gib: 0.25, in_use_gib: 0.125 }));
    }
}
