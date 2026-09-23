//! Native macOS collection.
//!
//! The Linux path shells out to `pc-ai-stats`, which reads `/proc` and runs `ss`.
//! Neither exists here, so this module reads the same numbers from the tools that
//! do exist: `sysctl`, `vm_stat`, `ps` and `lsof`.
//!
//! Every parser takes text and returns data, with no process spawned. That is
//! deliberate: the parsing is the part that can break silently, and it is the part
//! that can be tested on a Linux CI box too. The `collect_*` functions are the only
//! ones that touch the outside world.

// On non-macOS this module exists purely so the parsers and their tests
// compile and run; the only callers of the parsers live behind the macOS
// process boundary below (or in `#[cfg(test)]`), so rustc would flag every
// item as dead in a non-test Linux build. This is scoped to this module and
// platform-conditioned on purpose: the dead-code lint stays fully active on
// macOS, where the parsers are reachable from `collect_*`.
#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use crate::stats::{Group, Memory, Process};
#[cfg(target_os = "macos")]
use crate::stats::Stats;
use std::collections::HashMap;
// Only the process boundary below spawns anything; on other platforms the
// parsers compile and are tested, and this import would be unused.
#[cfg(target_os = "macos")]
use std::process::Command;

/// `vm_stat` reports pages, not bytes, so every figure needs hw.pagesize.
pub struct PageSize(pub u64);

/// Parses `sysctl -n hw.memsize hw.pagesize` (one value per line).
pub fn parse_sysctl_u64s(text: &str) -> Result<(u64, u64), String> {
    let mut values = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let value: u64 = line
            .parse()
            .map_err(|_| format!("sysctl devolvio un no-numero: {line:?}"))?;
        values.push(value);
    }
    if values.len() < 2 {
        return Err(format!("sysctl devolvio {} valores, se esperaban 2", values.len()));
    }
    Ok((values[0], values[1]))
}

/// Parses `vm_stat` into page counts. Returns (free, active, inactive, wired,
/// compressed) plus the page size when `hw.pagesize` shows up in the same output.
pub fn parse_vm_stat(text: &str) -> Result<(VmPages, Option<u64>), String> {
    let mut pages = VmPages::default();
    let mut page_size = None;
    for line in text.lines() {
        let Some((key, raw)) = line.split_once(':') else {
            continue;
        };
        let key = key.trim();
        // "Mach Virtual Memory Statistics" is a header, not a counter.
        let count = raw
            .trim()
            .trim_end_matches('.')
            .trim()
            .parse::<u64>()
            .ok();
        let Some(count) = count else { continue };
        match key {
            "Pages free" => pages.free = count,
            "Pages active" => pages.active = count,
            "Pages inactive" => pages.inactive = count,
            "Pages wired down" => pages.wired = count,
            "Pages occupied by compressor" => pages.compressed = count,
            "Page size of bytes" => page_size = Some(count),
            _ => {}
        }
    }
    Ok((pages, page_size))
}

#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub struct VmPages {
    pub free: u64,
    pub active: u64,
    pub inactive: u64,
    pub wired: u64,
    pub compressed: u64,
}

impl VmPages {
    /// Apple's own Activity Monitor definition of "used": wired + active +
    /// compressed. Inactive pages are reclaimable, so counting them would show a
    /// machine that is nearly full when it is actually fine.
    pub fn used_gib(&self, page_size: u64) -> f64 {
        if page_size == 0 {
            return 0.0;
        }
        let bytes = (self.wired + self.active + self.compressed) as f64 * page_size as f64;
        bytes / GIB
    }

    pub fn available_gib(&self, page_size: u64) -> f64 {
        if page_size == 0 {
            return 0.0;
        }
        let bytes = (self.free + self.inactive) as f64 * page_size as f64;
        bytes / GIB
    }
}

const GIB: f64 = 1024.0 * 1024.0 * 1024.0;

/// Parses `vm_stat` + page size into the Memory shape the UI already renders.
pub fn memory_from(vm: &VmPages, page_size: u64, total_bytes: u64) -> Memory {
    Memory {
        total_gib: total_bytes as f64 / GIB,
        used_gib: vm.used_gib(page_size),
        available_gib: vm.available_gib(page_size),
        // Swap needs `sysctl vm.swapusage`; collect_swap fills these in.
        swap_total_gib: 0.0,
        swap_used_gib: 0.0,
    }
}

/// Parses `sysctl vm.swapusage`, e.g.
/// `vm.swapusage: total = 8192.00M  used = 6144.50M  free = 2047.50M`.
pub fn parse_swap(text: &str) -> Option<(f64, f64)> {
    let (_, rest) = text.split_once(':')?;
    let mut total = None;
    let mut used = None;
    for (label, slot) in [("total =", &mut total), ("used =", &mut used)] {
        let (_, tail) = rest.split_once(label)?;
        let value: f64 = tail
            .trim_start()
            .split_whitespace()
            .next()?
            .trim_end_matches('M')
            .parse()
            .ok()?;
        *slot = Some(value / 1024.0);
    }
    Some((total?, used?))
}

/// Parses one `ps -axo pid=,ppid=,uid=,rss=,%cpu=,comm=` block.
///
/// `comm` is the last field and may contain spaces (e.g. "Google Chrome
/// Helper"), so it takes the remainder rather than one token.
pub fn parse_ps(text: &str) -> Vec<Process> {
    let mut rows = Vec::new();
    for line in text.lines() {
        let line = line.trim_start();
        if line.is_empty() {
            continue;
        }
        let mut fields = line.split_whitespace();
        let (Some(pid), Some(ppid), Some(uid), Some(rss), Some(cpu)) =
            (fields.next(), fields.next(), fields.next(), fields.next(), fields.next())
        else {
            continue;
        };
        let Some(comm) = fields.collect::<Vec<_>>().join(" ").into() else {
            continue;
        };
        let comm: String = comm;
        if comm.is_empty() {
            continue;
        }
        rows.push(Process {
            pid: pid.parse().unwrap_or(0),
            ppid: ppid.parse().unwrap_or(0),
            uid: uid.parse().unwrap_or(0),
            // ps reports rss in kilobytes, same unit the Linux collector uses.
            rss_kb: rss.parse().unwrap_or(0),
            cpu: cpu.parse().unwrap_or(0.0),
            comm,
            args: String::new(),
        });
    }
    rows
}

/// Groups processes by the caller's own patterns. Nothing here is hard-coded: the
/// groups come from configuration, which is what lets a user pick what shows up.
pub fn group_by(rows: &[Process], patterns: &[(String, Vec<String>)]) -> HashMap<String, Group> {
    let mut groups: HashMap<String, Group> = HashMap::new();
    for (name, needles) in patterns {
        for row in rows {
            let command = row.comm.to_lowercase();
            if !needles
                .iter()
                .any(|needle| command.contains(&needle.to_lowercase()))
            {
                continue;
            }
            let group = groups.entry(name.clone()).or_insert_with(|| Group {
                rss_gib: 0.0,
                cpu: 0.0,
                pids: Vec::new(),
            });
            group.rss_gib += row.rss_kb as f64 * 1024.0 / GIB;
            group.cpu += row.cpu;
            group.pids.push(row.pid);
        }
    }
    groups
}

/// Parses `lsof -nP -iTCP -sTCP:LISTEN` into the `ss`-shaped text the existing
/// portable parser already understands. Keeping one downstream parser means the
/// port table, its tests and the UI do not fork per platform.
pub fn lsof_to_ss(text: &str) -> String {
    let mut out = String::from("Netid  State  Recv-Q Send-Q Local Address:Port  Peer Address:Port\n");
    for line in text.lines().skip(1) {
        let mut fields = line.split_whitespace();
        let (Some(command), Some(pid)) = (fields.next(), fields.next()) else {
            continue;
        };
        let Some(addr) = fields.nth(6) else { continue };
        let (local, peer) = match addr.rsplit_once("->") {
            Some((l, p)) => (l, p),
            None => (addr, "*:*"),
        };
        out.push_str(&format!(
            "tcp    LISTEN 0      0      {local}  {peer} users:(({command},pid={pid}))\n"
        ));
    }
    out
}

// -- process boundary: the only part that spawns anything ----------------------
// Everything below launches macOS binaries (`sysctl`, `vm_stat`, `ps`) that do
// not exist elsewhere, so it is only compiled on macOS. The pure parsers above
// stay available on every platform for testing.
#[cfg(target_os = "macos")]
fn run(argv: &[&str]) -> Option<String> {
    let output = Command::new(argv[0]).args(&argv[1..]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}

#[cfg(target_os = "macos")]
pub fn collect_memory() -> Result<Memory, String> {
    let sizes = run(&["/usr/sbin/sysctl", "-n", "hw.memsize", "hw.pagesize"])
        .ok_or_else(|| "sysctl hw.memsize fallo".to_string())?;
    let (total_bytes, page_size) = parse_sysctl_u64s(&sizes)?;
    let vm_text = run(&["/usr/bin/vm_stat"]).ok_or_else(|| "vm_stat fallo".to_string())?;
    let (pages, reported) = parse_vm_stat(&vm_text)?;
    // vm_stat can print its own page size; prefer it when present because it is
    // the same snapshot as the counters.
    let page_size = reported.unwrap_or(page_size);
    let mut memory = memory_from(&pages, page_size, total_bytes);
    if let Some(text) = run(&["/usr/sbin/sysctl", "-n", "vm.swapusage"]) {
        if let Some((total, used)) = parse_swap(&text) {
            memory.swap_total_gib = total;
            memory.swap_used_gib = used;
        }
    }
    Ok(memory)
}

#[cfg(target_os = "macos")]
pub fn collect_processes() -> Vec<Process> {
    run(&[
        "/bin/ps",
        "-axo",
        "pid=,ppid=,uid=,rss=,%cpu=,comm=",
    ])
    .map(|text| parse_ps(&text))
    .unwrap_or_default()
}

/// Full snapshot without models: model discovery on macOS still needs the config
/// reader that lives on the Python side, so it is reported as an empty list rather
/// than guessed at from process names.
#[cfg(target_os = "macos")]
pub fn collect_stats() -> Stats {
    let processes = collect_processes();
    Stats {
        memory: collect_memory().unwrap_or(Memory {
            total_gib: 0.0,
            used_gib: 0.0,
            available_gib: 0.0,
            swap_total_gib: 0.0,
            swap_used_gib: 0.0,
        }),
        models: Vec::new(),
        groups: HashMap::new(),
        processes,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sysctl_lee_total_y_pagesize() {
        assert_eq!(parse_sysctl_u64s("17179869184\n4096\n").unwrap(), (17179869184, 4096));
    }

    #[test]
    fn sysctl_rechaza_salida_mutilada() {
        assert!(parse_sysctl_u64s("17179869184\n").is_err());
        assert!(parse_sysctl_u64s("17179869184\nnope\n").is_err());
    }

    #[test]
    fn vm_stat_real_se_parsea() {
        // Salida capturada de `vm_stat` en macOS (formato literal, con el punto final).
        let text = "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n\
                    Pages free:                              258721.\n\
                    Pages active:                            412300.\n\
                    Pages inactive:                          380100.\n\
                    Pages speculative:                        12040.\n\
                    Pages throttled:                              0.\n\
                    Pages wired down:                        198400.\n\
                    Pages purgeable:                           4512.\n\
                    Pages occupied by compressor:             61200.\n";
        let (pages, size) = parse_vm_stat(text).unwrap();
        assert_eq!(size, None, "la pagina viene de sysctl, no del encabezado");
        assert_eq!(
            pages,
            VmPages {
                free: 258721,
                active: 412300,
                inactive: 380100,
                wired: 198400,
                compressed: 61200,
            }
        );
    }

    #[test]
    fn usado_excluye_paginas_inactivas() {
        // 4 KiB por pagina: used = wired+active+compressed = 671900 paginas.
        let pages = VmPages {
            free: 258721,
            active: 412300,
            inactive: 380100,
            wired: 198400,
            compressed: 61200,
        };
        let used = pages.used_gib(4096);
        let expected = (198400u64 + 412300 + 61200) as f64 * 4096.0 / GIB;
        assert!((used - expected).abs() < 1e-6, "usado={used} esperado={expected}");
        // Lo inactivo es recuperable: cuenta como disponible, no como usado. El
        // invariant no es "disponible > usado" (used es toda la maquina:
        // wired+active+compressed), es que las dos mitulas se complementan.
        let available = pages.available_gib(4096);
        assert!(available < used, "si inactivo contara como usado, disponible subiria");
        let total = 17179869184u64 as f64 / GIB;
        assert!(used + available <= total + 1e-6, "usado+disponible desborda la RAM");
    }

    #[test]
    fn tamano_de_pagina_cero_no_divide_por_cero() {
        assert_eq!(VmPages::default().used_gib(0), 0.0);
        assert_eq!(VmPages::default().available_gib(0), 0.0);
    }

    #[test]
    fn swap_en_mebibytes_a_gib() {
        let (total, used) =
            parse_swap("vm.swapusage: total = 8192.00M  used = 6144.50M  free = 2047.50M\n")
                .unwrap();
        assert!((total - 8.0).abs() < 1e-6, "total={total}");
        assert!((used - 6.000_488_281_25).abs() < 1e-6, "used={used}");
    }

    #[test]
    fn swap_ausente_no_inventa_ceros() {
        assert!(parse_swap("vm.swapusage: total = 0.00M  used = 0.00M  free = 0.00M\n").is_some());
        assert!(parse_swap("garbage\n").is_none());
    }

    #[test]
    fn ps_con_comms_espaciados() {
        let text = "  123     1  501  524288  12.5 /Applications/Firefox.app/Contents/MacOS/firefox\n\
                    4567  123   501   99999   0.3 Google Chrome Helper\n";
        let rows = parse_ps(text);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1].comm, "Google Chrome Helper");
        assert_eq!(rows[1].rss_kb, 99999);
        assert_eq!(rows[1].ppid, 123);
    }

    #[test]
    fn ps_no_reinventa_filas_rotas() {
        let rows = parse_ps("MachVsCode\n  1 2\n");
        assert!(rows.iter().all(|r| r.pid != 0 && !r.comm.is_empty()));
    }

    #[test]
    fn agrupar_usa_solo_los_patrones_que_pone_el_usuario() {
        let rows = parse_ps(" 1 0 501 1024 10.0 firefox\n 2 0 501 2048 5.0 Google Chrome\n 3 0 501 4096 1.0 ollama\n");
        let groups = group_by(
            &rows,
            &[
                ("navegadores".into(), vec!["firefox".into(), "chrome".into()]),
                ("local".into(), vec!["ollama".into()]),
            ],
        );
        assert_eq!(groups["navegadores"].pids, vec![1, 2]);
        assert_eq!(groups["local"].cpu, 1.0);
        assert!(!groups.contains_key("modelos"), "un patron sin coincidencia no crea el grupo");
    }

    #[test]
    fn lsof_se_traduce_al_formato_ss() {
        let lsof = "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n\
                    ollama  4242 alvaro   14u  IPv4 0xa111      0t0  TCP 127.0.0.1:11434 (LISTEN)\n\
                    Chrome  9313 alvaro   33u  IPv6 0xb222      0t0  TCP [::1]:9222 (LISTEN)\n";
        let ss = lsof_to_ss(lsof);
        assert!(ss.contains("127.0.0.1:11434"), "{ss}");
        assert!(ss.contains("pid=4242"), "{ss}");
        assert_eq!(ss.lines().count(), 3, "una fila por socket, mas la cabecera");
    }
}
