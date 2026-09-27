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

use crate::stats::{Group, GpuMemory, Memory, Process};
#[cfg(target_os = "macos")]
use crate::stats::Stats;
use std::collections::{HashMap, HashSet};
// Only the process boundary below spawns anything; on other platforms the
// parsers compile and are tested, and this import would be unused.
#[cfg(target_os = "macos")]
use std::process::Command;


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

/// Parses `ioreg -l -w 0` into machine-level GPU memory.
///
/// Every IOAccelerator node (real `AGXAccelerator` silicon or the
/// `AppleParavirtGPU` of a VM; device names are never matched) carries a
/// `PerformanceStatistics` dictionary with the two counters in bytes:
/// `"Alloc system memory"` and `"In use system memory"`. The rules, all
/// observable in `tests/fixtures/macos-capture.txt`:
///
/// * The same node shows up more than once in one dump — its dictionary is
///   repeated verbatim under `Entries`/`IOCompatibilityProperties` — so
///   dictionaries are deduplicated by their exact text. Two distinct GPUs that
///   reported byte-identical (fully idle) dictionaries would be counted once,
///   which undercounts zero bytes; summing duplicates doubles one real GPU,
///   which the capture shows happening.
/// * `"In use system memory (driver)"` is ignored on purpose: it is the
///   driver-attributed slice already included in `"In use system memory"`
///   (equal to it in the capture), so summing it would double the count.
/// * Dictionaries that do not report the whole pair (empty, or unrelated
///   counters like `vramFreeBytes`) are skipped, as is a non-numeric value
///   where a byte count belongs: half a measurement is no measurement.
/// * No usable dictionary means "cannot measure": `None`, never zero — zero
///   reads as "the GPU is idle", which is a different claim.
pub fn parse_ioreg_gpu(text: &str) -> Option<GpuMemory> {
    const KEY: &str = "\"PerformanceStatistics\"";
    let mut seen: HashSet<&str> = HashSet::new();
    let mut alloc_bytes = 0u64;
    let mut in_use_bytes = 0u64;
    let mut measured = false;
    let mut rest = text;
    while let Some(at) = rest.find(KEY) {
        let after_key = &rest[at + KEY.len()..];
        rest = after_key;
        // The dictionary must open right after the key, with only ` = ` in
        // between; anything else means this occurrence is not a property
        // assignment and the scan moves on.
        let Some(open) = after_key.find('{') else { break };
        if !after_key[..open].chars().all(|c| c.is_whitespace() || c == '=') {
            continue;
        }
        let Some(close) = dict_close(&after_key[open..]) else {
            // Truncated dictionary: there is nothing trustworthy left to read.
            break;
        };
        let dict = &after_key[open..=open + close];
        rest = &after_key[open + close + 1..];
        if !seen.insert(dict) {
            continue; // same node repeated verbatim: counting it again doubles it
        }
        let Some((alloc, in_use)) = gpu_dict_values(dict) else { continue };
        measured = true;
        alloc_bytes += alloc;
        in_use_bytes += in_use;
    }
    measured.then(|| GpuMemory {
        alloc_gib: alloc_bytes as f64 / GIB,
        in_use_gib: in_use_bytes as f64 / GIB,
    })
}

/// Index (inside `s`, which starts at the opening `{`) of the `}` that closes
/// the dictionary. Brace-aware and quote-aware: values may be quoted strings
/// containing braces, and the captured output nests dictionaries freely.
fn dict_close(s: &str) -> Option<usize> {
    let mut depth = 0usize;
    let mut in_quotes = false;
    let mut escaped = false;
    for (idx, c) in s.char_indices() {
        if in_quotes {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == '"' {
                in_quotes = false;
            }
            continue;
        }
        match c {
            '"' => in_quotes = true,
            '{' => depth += 1,
            '}' => {
                depth = depth.checked_sub(1)?;
                if depth == 0 {
                    return Some(idx);
                }
            }
            _ => {}
        }
    }
    None
}

/// The `(alloc, in_use)` byte pair of one `PerformanceStatistics` dictionary,
/// or `None` when the pair is not fully reported there.
fn gpu_dict_values(dict: &str) -> Option<(u64, u64)> {
    let mut alloc = None;
    let mut in_use = None;
    for (key, value) in perf_dict_entries(dict) {
        match key {
            "Alloc system memory" => alloc = value.parse().ok(),
            "In use system memory" => in_use = value.parse().ok(),
            _ => {} // recoveryCount, vramFreeBytes, ...: not part of this measurement
        }
    }
    Some((alloc?, in_use?))
}

/// Splits one `PerformanceStatistics` dictionary body into its `"key"=value`
/// pairs. Keys contain spaces and parentheses ("In use system memory
/// (driver)"), so this is a small scanner rather than a `split(',')`; values
/// may be quoted strings holding their own commas or braces.
fn perf_dict_entries(dict: &str) -> Vec<(&str, &str)> {
    let body = dict
        .strip_prefix('{')
        .and_then(|d| d.strip_suffix('}'))
        .unwrap_or(dict);
    let mut entries = Vec::new();
    let mut i = 0;
    while i < body.len() {
        let Some(c) = body[i..].chars().next() else { break };
        if c.is_whitespace() || c == ',' {
            i += c.len_utf8();
            continue;
        }
        if c != '"' {
            // Malformed token: skip past the next separator and keep going.
            match body[i..].find([',', '}']) {
                Some(j) => i += j,
                None => break,
            }
            continue;
        }
        let key_start = i + 1;
        let Some(key_len) = body[key_start..].find('"') else { break };
        let key = &body[key_start..key_start + key_len];
        i = key_start + key_len + 1;
        // Only whitespace may sit between the key and its `=`.
        let Some(after_eq) = body[i..].trim_start().strip_prefix('=') else { continue };
        let value_at = body.len() - after_eq.len();
        let value_at = value_at + (body[value_at..].len() - body[value_at..].trim_start().len());
        if body[value_at..].starts_with('"') {
            let vstart = value_at + 1;
            let Some(vend) = body[vstart..].find('"') else { break };
            entries.push((key, &body[vstart..vstart + vend]));
            i = vstart + vend + 1;
        } else {
            let end = body[value_at..]
                .find(',')
                .map_or(body.len(), |j| value_at + j);
            entries.push((key, body[value_at..end].trim()));
            i = end;
        }
    }
    entries
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

/// Machine-level GPU memory from the live registry. `ioreg` is read-only and
/// needs no privileges; a VM's paravirtual GPU reports the same
/// PerformanceStatistics dictionary as retail silicon.
#[cfg(target_os = "macos")]
pub fn collect_gpu() -> Option<GpuMemory> {
    let text = run(&["/usr/sbin/ioreg", "-l", "-w", "0"])?;
    parse_ioreg_gpu(&text)
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
        gpu: collect_gpu(),
        groups: HashMap::new(),
        processes,
    }
}

// -- smoke contra el sistema real (solo macOS) ---------------------------------
//
// El modulo `tests` de abajo alimenta los parsers con texto capturado: no
// prueba nada del limite de procesos. Estos tests EJECUTAN las funciones
// collect_* sobre la maquina donde corre cargo, para que un binario con mala
// ruta o un formato de herramienta que cambio se ponga rojo aca en vez de
// quedar verde detras de un parser que nunca se ejecuta. Por eso el gate es
// target_os = "macos": en Linux los binarios no existen y el CI no debe ponerse
// rojo por la razon equivocada.
#[cfg(all(test, target_os = "macos"))]
mod smoke {
    use super::*;

    #[test]
    fn collect_memory_mide_la_maquina_real() {
        let mem =
            collect_memory().expect("collect_memory fallo en un macOS real: binario, ruta o formato de sysctl/vm_stat roto");

        assert!(
            mem.total_gib > 0.0,
            "total_gib={} <= 0: hw.memsize no llego o no se parseo",
            mem.total_gib
        );

        // Tamano de pagina: propiedad, no constante. 16384 en Apple Silicon,
        // 4096 en Intel; el valor capturado no debe terminar hardcodeado en un
        // assert.
        let sizes = run(&["/usr/sbin/sysctl", "-n", "hw.memsize", "hw.pagesize"])
            .expect("sysctl hw.memsize hw.pagesize fallo en un macOS real");
        let (total_bytes, page_size) =
            parse_sysctl_u64s(&sizes).expect("sysctl devolvio algo que el parser no entiende");
        assert!(page_size >= 4096, "page_size={} < 4096", page_size);
        assert!(
            page_size.is_power_of_two(),
            "page_size={} no es potencia de dos",
            page_size
        );
        assert_eq!(
            total_bytes as f64 / GIB,
            mem.total_gib,
            "el total de sysctl no coincide con el de collect_memory"
        );

        assert!(
            mem.used_gib >= 0.0,
            "used_gib={} es negativo o NaN",
            mem.used_gib
        );
        assert!(
            mem.available_gib >= 0.0,
            "available_gib={} es negativo o NaN",
            mem.available_gib
        );
        assert!(
            mem.used_gib <= mem.total_gib,
            "used_gib={} > total_gib={}: unidades rotas o parsing de vm_stat roto",
            mem.used_gib,
            mem.total_gib
        );
        assert!(
            mem.available_gib <= mem.total_gib,
            "available_gib={} > total_gib={}: unidades rotas o parsing de vm_stat roto",
            mem.available_gib,
            mem.total_gib
        );

        // NO se afirma `used + available <= total` a proposito: las categorias
        // de vm_stat son disjuntas por nombre, pero esa disjuncion no se pudo
        // verificar en hardware real (el runner macos-14 es una VM), y un assert
        // inestable en CI es peor que uno mas debil que siempre vale.
    }

    #[test]
    fn collect_gpu_cuando_mide_no_excede_la_ram() {
        // None es un resultado legitimo: la captura del runner demuestra que una
        // maquina puede no reportar la pareja de contadores completa.
        let Some(gpu) = collect_gpu() else { return };
        let mem = collect_memory()
            .expect("collect_memory fallo en un macOS real mientras se verificaba collect_gpu");

        assert!(
            gpu.alloc_gib.is_finite() && gpu.alloc_gib >= 0.0,
            "alloc_gib={} no es finito o es negativo",
            gpu.alloc_gib
        );
        assert!(
            gpu.in_use_gib.is_finite() && gpu.in_use_gib >= 0.0,
            "in_use_gib={} no es finito o es negativo",
            gpu.in_use_gib
        );

        // NO se afirma orden entre los dos contadores a proposito: la captura del
        // propio runner (paravirtualizado) reporta lo inverso a la intuicion --
        //   "Alloc system memory"=39108608 < "In use system memory"=50103936
        // -- asi que cualquiera de los dos puede superar al otro. Cual semantica
        // es la correcta queda por decidir en silicio real (odd/tasks/macos-runbook.md).
        //
        // Lo que SI vale: memoria unificada, una reserva de GPU no puede exceder
        // la RAM del sistema. Esto agarra el error realista de unidades
        // (bytes-vs-GiB): 39 MB reportados como 39 GiB revientan contra total.
        assert!(
            gpu.alloc_gib <= mem.total_gib,
            "alloc_gib={} > total_gib={}: error de unidades (bytes vs GiB) o de suma",
            gpu.alloc_gib,
            mem.total_gib
        );
        assert!(
            gpu.in_use_gib <= mem.total_gib,
            "in_use_gib={} > total_gib={}: error de unidades (bytes vs GiB) o de suma",
            gpu.in_use_gib,
            mem.total_gib
        );
    }

    #[test]
    fn collect_processes_ve_procesos_reales() {
        let rows = collect_processes();
        assert!(
            !rows.is_empty(),
            "ps -axo pid=,ppid=,uid=,rss=,%cpu=,comm= no devolvio filas: ruta, flags o formato de ps rotos"
        );
        for row in &rows {
            assert!(
                row.pid != 0,
                "pid 0 en la salida (pid no parseable por parse_ps, o pid 0 real filtrado donde no debia): comm={:?}",
                row.comm
            );
            assert!(!row.comm.is_empty(), "comm vacio para pid {}", row.pid);
        }
    }

    #[test]
    fn collect_stats_arma_un_snapshot_sin_panic() {
        let stats = collect_stats();
        assert!(
            stats.memory.total_gib > 0.0,
            "collect_stats devolvio memoria en ceros: collect_memory fallo adentro y se uso el fallback"
        );
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

    // -- GPU (ioreg) -----------------------------------------------------------

    // Lineas literales de src-tauri/tests/fixtures/macos-capture.txt: la misma
    // GPU paravirtual aparece dos veces (seccion ioreg-accelerator y
    // ioreg-perfstats) y su diccionario con "vramFreeBytes" otras dos dentro de
    // entradas de compatibilidad. Cuatro apariciones, dos diccionarios unicos.
    const CAPTURE_PERF_LINES: &str = concat!(
        r#"    "PerformanceStatistics" = {"Alloc system memory"=39108608,"In use system memory"=50103936,"In use system memory (driver)"=50103936,"recoveryCount"=0}"#,
        "\n",
        r#"    "Entries" = ({"IOName"="display","IOClass"="IOPCIDevice","device-id"=<10680000>,"PerformanceStatistics"={"vramFreeBytes"=15728640},"IOGLBundleName"="AppleMetalGLRenderer","VRAM,totalMB"=16384})"#,
        "\n",
        r#"    "IOCompatibilityProperties" = {"IOName"="IOAccelerator","PerformanceStatistics"={"vramFreeBytes"=15728640},"MetalPluginName"="AGXMetalA12"}"#,
        "\n",
        r#"    | |   "PerformanceStatistics" = {"Alloc system memory"=39108608,"In use system memory"=50103936,"In use system memory (driver)"=50103936,"recoveryCount"=0}"#,
        "\n",
    );

    #[test]
    fn ioreg_captura_real_no_duplica_el_total() {
        let gpu = parse_ioreg_gpu(CAPTURE_PERF_LINES).expect("la captura si reporta la GPU");
        assert_eq!(gpu.alloc_gib, 39108608f64 / GIB);
        assert_eq!(gpu.in_use_gib, 50103936f64 / GIB);
        let duplicado = 2.0 * 39108608f64 / GIB;
        assert!(gpu.alloc_gib < duplicado, "sumar a lo bruto duplica el nodo");
    }

    #[test]
    fn ioreg_dos_dispositivos_distintos_se_suman() {
        // Dos nodos con diccionarios distintos: los totales se suman. La clave
        // "In use system memory (driver)" debe ignorarse (si se sumara, el
        // primer nodo aportaria 1000 bytes de mas).
        let text = concat!(
            r#"  +-o AGXAccelerator  <class AGXAccelerator, id 0x1, registered>"#,
            "\n",
            r#"      "PerformanceStatistics" = {"Alloc system memory"=1073741824,"In use system memory"=536870912,"In use system memory (driver)"=1000,"recoveryCount"=2}"#,
            "\n",
            r#"  +-o AGXAccelerator  <class AGXAccelerator, id 0x2, registered>"#,
            "\n",
            r#"      "PerformanceStatistics" = {"Alloc system memory"=2147483648,"In use system memory"=1073741824,"recoveryCount"=0}"#,
            "\n",
        );
        let gpu = parse_ioreg_gpu(text).unwrap();
        assert_eq!(gpu.alloc_gib, 3.0);
        assert_eq!(gpu.in_use_gib, 1.5);
    }

    #[test]
    fn ioreg_diccionario_vacio_no_mide() {
        assert!(parse_ioreg_gpu(r#""PerformanceStatistics" = {}"#).is_none());
        assert!(parse_ioreg_gpu(r#""PerformanceStatistics" = {"recoveryCount"=0}"#).is_none());
    }

    #[test]
    fn ioreg_sin_llave_de_cierre_no_inventa_valores() {
        let text = r#""PerformanceStatistics" = {"Alloc system memory"=39108608,"In use system memory"=50103936"#;
        assert!(parse_ioreg_gpu(text).is_none());
    }

    #[test]
    fn ioreg_valor_no_numerico_no_mide_a_medias() {
        let text = r#""PerformanceStatistics" = {"Alloc system memory"="desconocido","In use system memory"=50103936}"#;
        assert!(parse_ioreg_gpu(text).is_none());
    }

    #[test]
    fn ioreg_sin_ningun_performance_statistics_devuelve_none() {
        assert!(parse_ioreg_gpu("+-o IOService  <class IOService>\n nada util\n").is_none());
        assert!(parse_ioreg_gpu("").is_none());
    }

    #[test]
    fn ioreg_llaves_dentro_de_comillas_no_cortan_el_diccionario() {
        let text = r#""PerformanceStatistics" = {"Model"="Radeon }{ raro","Alloc system memory"=2048,"In use system memory"=1024}"#;
        let gpu = parse_ioreg_gpu(text).unwrap();
        assert_eq!(gpu.alloc_gib, 2048f64 / GIB);
        assert_eq!(gpu.in_use_gib, 1024f64 / GIB);
    }
}
