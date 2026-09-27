use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Memory {
    pub total_gib: f64,
    pub used_gib: f64,
    pub available_gib: f64,
    pub swap_total_gib: f64,
    pub swap_used_gib: f64,
}

/// `gtt_gib` / `vram_gib` are `None` when per-model GPU memory cannot be
/// measured (macOS has no public API for it); the Linux collector (Python
/// script) always sends numbers, so its values deserialize as `Some(...)`,
/// including the real measurement `Some(0.0)` for "this model uses no GTT".
/// A `None` is "not measurable", never zero.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Model {
    pub alias: String,
    pub pid: i64,
    pub port: Option<i64>,
    pub model: String,
    pub rss_gib: f64,
    pub cpu: f64,
    pub gtt_gib: Option<f64>,
    pub vram_gib: Option<f64>,
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
    fn linux_manda_numeros_y_entran_como_some() {
        // El colector de Linux (script Python) siempre manda números para
        // gtt_gib/vram_gib, incluido el 0.0 real de "este modelo no usa GTT".
        let json = r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
            "swap_total_gib": 0.0, "swap_used_gib": 0.0},
            "models": [{"alias": "a", "pid": 1, "port": 8080, "model": "m",
            "rss_gib": 1.5, "cpu": 2.0, "gtt_gib": 40.6, "vram_gib": 0.0}],
            "groups": {}, "processes": []}"#;
        let stats: Stats = serde_json::from_str(json).unwrap();
        assert_eq!(stats.models.len(), 1);
        assert_eq!(stats.models[0].gtt_gib, Some(40.6));
        assert_eq!(stats.models[0].vram_gib, Some(0.0));
    }

    #[test]
    fn macos_sin_valor_deja_gtt_y_vram_en_none() {
        // En macOS no hay API pública de memoria GPU por proceso: el campo no
        // lleva valor (va vacío) y debe entrar como None, no como 0.0.
        let json = r#"{"memory": {"total_gib": 1.0, "used_gib": 0.5, "available_gib": 0.5,
            "swap_total_gib": 0.0, "swap_used_gib": 0.0},
            "models": [{"alias": "a", "pid": 1, "port": null, "model": "m",
            "rss_gib": 1.5, "cpu": 2.0}],
            "groups": {}, "processes": []}"#;
        let stats: Stats = serde_json::from_str(json).unwrap();
        assert_eq!(stats.models.len(), 1);
        assert_eq!(stats.models[0].gtt_gib, None);
        assert_eq!(stats.models[0].vram_gib, None);
    }

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
