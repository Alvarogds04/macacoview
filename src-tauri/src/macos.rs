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

use crate::config::WatchEntry;
use crate::ports::parse_ss_output;
use crate::stats::{Group, GpuMemory, Memory, Model, Process};
use regex::Regex;
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

/// Groups processes exactly the way the Linux collector does
/// (`scripts/pc-ai-stats`): pattern entries are matched first, in config
/// order, as regular expressions searched against the process name (comm)
/// and its full command line; a `system` entry without patterns then catches
/// root (uid 0) processes; any other visible entry without patterns catches
/// everything no pattern claimed. A process lands in exactly one group. The
/// regexes come from `config.toml`, which is what lets a user pick what
/// shows up; a broken pattern is skipped, never fatal.
pub fn group_by(rows: &[Process], watch: &[WatchEntry]) -> HashMap<String, Group> {
    // Pattern entries decide first (config order), then the fixed root rule
    // and finally the catch-all entry, mirroring the Linux collector. Every
    // visible entry keeps its group even at zero, so "system" and "other"
    // show up empty instead of vanishing.
    let pattern_entries: Vec<&WatchEntry> = watch
        .iter()
        .filter(|entry| entry.visible && !entry.patterns.is_empty())
        .collect();
    let system_entry = watch
        .iter()
        .find(|entry| entry.visible && entry.patterns.is_empty() && entry.name == "system");
    let fallback_entry = watch
        .iter()
        .find(|entry| entry.visible && entry.patterns.is_empty() && entry.name != "system");

    let mut groups: HashMap<String, Group> = watch
        .iter()
        .filter(|entry| entry.visible)
        .map(|entry| {
            (
                entry.name.clone(),
                Group {
                    rss_gib: 0.0,
                    cpu: 0.0,
                    pids: Vec::new(),
                },
            )
        })
        .collect();

    for row in rows {
        let mut target = pattern_entries
            .iter()
            .find(|entry| row_matches(entry.patterns.as_slice(), row))
            .map(|entry| entry.name.as_str());
        if target.is_none() && row.uid == 0 {
            target = system_entry.map(|entry| entry.name.as_str());
        }
        if target.is_none() {
            target = fallback_entry.map(|entry| entry.name.as_str());
        }
        let Some(name) = target else { continue };
        let group = groups.get_mut(name).expect("el grupo viene de las entradas visibles");
        group.rss_gib += row.rss_kb as f64 * 1024.0 / GIB;
        group.cpu += row.cpu;
        group.pids.push(row.pid);
    }
    groups
}

/// Whether any pattern claims the row. A pattern is a regular expression
/// searched against the process name and its full command line, exactly like
/// `_matches` in `scripts/pc-ai-stats`; a broken regex is skipped, never
/// fatal.
fn row_matches(patterns: &[String], row: &Process) -> bool {
    patterns.iter().any(|pattern| {
        let Ok(re) = Regex::new(pattern) else { return false };
        re.is_match(&row.comm) || re.is_match(&row.args)
    })
}

/// Parses `lsof -nP -iTCP -sTCP:LISTEN` into the `ss`-shaped text the existing
/// portable parser already understands. Keeping one downstream parser means the
/// port table, its tests and the UI do not fork per platform. The `users:(...)`
/// field is emitted exactly as `ss` writes it — quoted command name — because
/// `parse_ss_output` extracts the pid with a regex that requires the quotes;
/// an unquoted bridge would drop every pid downstream.
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
            "tcp    LISTEN 0      0      {local}  {peer} users:((\"{command}\",pid={pid}))\n"
        ));
    }
    out
}

// -- deteccion de modelos locales ---------------------------------------------
//
// En Darwin no hay /proc ni config del colector de Linux: los modelos locales
// se descubren desde el argv de `ps -ww -axo pid=,ppid=,uid=,rss=,%cpu=,args=`
// (`-ww` evita truncar los flags largos con las rutas de los modelos) mas la
// salida de `ollama ps` para los modelos cargados en Ollama. El runner interno
// de Ollama es un hijo `llama-server` cuyo argv apunta a un blob sin nombre
// util, asi que se excluye del camino llama.cpp (ver `is_ollama_runner`) y sus
// modelos entran por la captura propia de `ollama ps` — herramienta del
// sistema, no cliente HTTP.

/// Parses `ps -ww -axo pid=,ppid=,uid=,rss=,%cpu=,args=`.
///
/// Five fixed numeric columns and then `args` as the free remainder of the
/// line, spaces included. `comm=` is never requested together with `args=`:
/// macOS truncates it to MAXCOMLEN (16 chars) exactly when `args` is also
/// asked for, as the real capture in `tests/fixtures/macos-capture.txt`
/// (section `ps-args`) shows. The `comm` field is filled best-effort from the
/// first argv token so the `Process` shape stays intact.
pub fn parse_ps_args(text: &str) -> Vec<Process> {
    let mut rows = Vec::new();
    for line in text.lines() {
        let line = line.trim_start();
        if line.is_empty() {
            continue;
        }
        let bytes = line.as_bytes();
        let mut fields = [""; 5];
        let mut i = 0usize;
        let mut taken = 0usize;
        while i < bytes.len() && taken < 5 {
            while i < bytes.len() && bytes[i].is_ascii_whitespace() {
                i += 1;
            }
            let start = i;
            while i < bytes.len() && !bytes[i].is_ascii_whitespace() {
                i += 1;
            }
            if i == start {
                break;
            }
            fields[taken] = &line[start..i];
            taken += 1;
        }
        if taken < 5 {
            continue;
        }
        // i sits just past the 5th column: everything after it is `args`.
        let args = line[i..].trim_start();
        if args.is_empty() {
            continue;
        }
        let comm = basename(args.split_whitespace().next().unwrap_or(args)).to_string();
        rows.push(Process {
            pid: fields[0].parse().unwrap_or(0),
            ppid: fields[1].parse().unwrap_or(0),
            uid: fields[2].parse().unwrap_or(0),
            // ps reports rss in kilobytes, same unit the Linux collector uses.
            rss_kb: fields[3].parse().unwrap_or(0),
            cpu: fields[4].parse().unwrap_or(0.0),
            comm,
            args: args.to_string(),
        });
    }
    rows
}

/// Detects a known local model server from argv tokens. Identity goes by how
/// the process was invoked, never by one exact binary path. Returns the model
/// identifier and the explicit `--alias` when the server supports one.
fn detect_server(tokens: &[&str]) -> Option<(String, Option<String>)> {
    let program = basename(tokens.first()?);

    // llama.cpp: `llama-server --alias X -m Y --port N` (short forms `-a`, `-m`).
    if program == "llama-server" {
        let model = flag_value(tokens, "--model", Some("-m"))?;
        return Some((
            model.to_string(),
            flag_value(tokens, "--alias", Some("-a")).map(str::to_string),
        ));
    }

    // Module loaders: `-m vllm...` / `-m mlx_lm...` behind any interpreter.
    if let Some(module) = python_module(tokens) {
        if module.starts_with("vllm") {
            // `python -m vllm serve MODEL` keeps the positional model; older
            // entry points take `--model` instead.
            let model = tokens
                .iter()
                .position(|t| *t == "serve")
                .and_then(|i| tokens.get(i + 1).copied())
                .or_else(|| flag_value(tokens, "--model", None))?;
            return Some((model.to_string(), None));
        }
        if module.starts_with("mlx_lm") {
            let model = flag_value(tokens, "--model", None)?;
            return Some((model.to_string(), None));
        }
    }

    // vLLM: `vllm serve MODEL --port N`.
    if program == "vllm" && tokens.get(1) == Some(&"serve") {
        return Some((tokens.get(2)?.to_string(), None));
    }

    // mlx-lm: `mlx_lm.server --model X --port N`.
    if program.starts_with("mlx_lm") {
        let model = flag_value(tokens, "--model", None)?;
        return Some((model.to_string(), None));
    }

    None
}

/// Value of a CLI flag given either as `--flag value` / `-f value` or
/// `--flag=value` / `-f=value`.
fn flag_value<'a>(tokens: &[&'a str], long: &str, short: Option<&str>) -> Option<&'a str> {
    for (i, tok) in tokens.iter().enumerate() {
        if *tok == long || Some(*tok) == short {
            return tokens.get(i + 1).copied();
        }
        if let Some(value) = tok.strip_prefix(long).filter(|rest| rest.starts_with('=')) {
            return Some(&value[1..]);
        }
        if let Some(s) = short {
            if let Some(value) = tok.strip_prefix(s).filter(|rest| rest.starts_with('=')) {
                return Some(&value[1..]);
            }
        }
    }
    None
}

/// The module name after a bare `-m` token, the way `python -m ...` carries it.
fn python_module<'a>(tokens: &[&'a str]) -> Option<&'a str> {
    tokens
        .iter()
        .position(|t| *t == "-m")
        .and_then(|i| tokens.get(i + 1).copied())
}

fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn strip_gguf(name: &str) -> &str {
    name.strip_suffix(".gguf").unwrap_or(name)
}

/// pid -> listening port, cross-referenced from `lsof -nP -iTCP
/// -sTCP:LISTEN` output through the existing `lsof_to_ss` bridge and the
/// portable `ss` parser. Servers that print their port in argv never need
/// this; the rest would otherwise stay portless.
pub fn listen_ports(lsof_text: &str) -> HashMap<i64, u16> {
    let ss = lsof_to_ss(lsof_text);
    let mut map: HashMap<i64, u16> = HashMap::new();
    for row in parse_ss_output(&ss) {
        if row.state != "LISTEN" || row.pid == 0 {
            continue;
        }
        map.entry(i64::from(row.pid)).or_insert(row.port);
    }
    map
}

/// Local model servers found in one `ps` snapshot, with listening-socket
/// fallback for their ports.
///
/// Field rules: `alias` is `--alias` when the server supports it, otherwise
/// the basename of the model path without its `.gguf` suffix; `port` comes
/// from argv first, from the listening sockets otherwise; `rss_gib`/`cpu`
/// come from the ps row. `gtt_gib` and `vram_gib` stay `None` on purpose:
/// macOS has no public per-process GPU memory API, and `None` means "not
/// measurable", never zero.
pub fn detect_models(rows: &[Process], listen: &HashMap<i64, u16>) -> Vec<Model> {
    // Los hijos de `ollama serve` se suprimen antes de derivar alias: su
    // --model es un blob del almacen de Ollama y el alias derivado seria
    // basura (ver `is_ollama_runner`).
    let ollama_serve = ollama_serve_pids(rows);
    let mut models = Vec::new();
    for row in rows {
        let tokens: Vec<&str> = row.args.split_whitespace().collect();
        let Some((model, alias_flag)) = detect_server(&tokens) else {
            continue;
        };
        if is_ollama_runner(&tokens, row, &ollama_serve) {
            continue;
        }
        let alias = alias_flag
            .unwrap_or_else(|| strip_gguf(basename(&model)).to_string());
        let port = flag_value(&tokens, "--port", None)
            .and_then(|p| p.parse::<i64>().ok())
            .or_else(|| listen.get(&row.pid).copied().map(i64::from));
        models.push(Model {
            alias,
            pid: row.pid,
            port,
            model,
            rss_gib: row.rss_kb as f64 * 1024.0 / GIB,
            cpu: row.cpu,
            gtt_gib: None,
            vram_gib: None,
        });
    }
    models
}

/// pids de los procesos `ollama serve` (el demonio de Ollama) en un snapshot
/// de ps. El argv puede venir como `ollama serve` o `/ruta/cualquiera/ollama
/// serve`: la identidad va por el basename, igual que en `detect_server`.
fn ollama_serve_pids(rows: &[Process]) -> HashSet<i64> {
    rows.iter()
        .filter(|row| {
            let mut tokens = row.args.split_whitespace();
            let is_ollama = basename(tokens.next().unwrap_or("")) == "ollama";
            is_ollama && tokens.any(|token| token == "serve")
        })
        .map(|row| row.pid)
        .collect()
}

/// Whether a detected model server is actually Ollama's internal runner, not a
/// standalone llama.cpp server. Two independent criteria, either one suffices:
///
/// 1. Parentage: a direct child of `ollama serve`. Ollama's architecture
///    spawns one backend server per loaded model from its daemon (real
///    capture, b54a80c: `ollama serve` pid 15442 -> `llama-server` pid 16327),
///    so any server whose parent is the daemon is Ollama's by construction.
///    This is the primary criterion because it depends on the daemon and not
///    on names or paths: it survives the child binary being renamed in future
///    Ollama versions and the model store being relocated (OLLAMA_MODELS).
/// 2. Model-path layout: `--model` points into Ollama's content-addressed
///    blob store (`<root>/blobs/sha256-<64 hex>`, where root defaults to
///    `~/.ollama/models` and moves wholesale with OLLAMA_MODELS). The
///    prefix and the `<root>/blobs/` shape are Ollama's storage layout, not a
///    user path convention, and a raw blob hash is never a usable model name
///    anyway — an alias derived from it is garbage. Catches the runner when
///    the daemon row is absent from the same ps snapshot.
///
/// What this deliberately does NOT assume: the child's binary name
/// (`llama-server` is vendored llama.cpp and may change) and any fixed
/// install path (Homebrew on ARM vs Intel, or a source build, all differ).
fn is_ollama_runner(tokens: &[&str], row: &Process, ollama_serve: &HashSet<i64>) -> bool {
    if ollama_serve.contains(&row.ppid) {
        return true;
    }
    flag_value(tokens, "--model", Some("-m"))
        .is_some_and(|model| model.contains("/blobs/sha256-"))
}

/// One loaded model as `ollama ps` reports it: the NAME and the SIZE column
/// parsed to bytes (`None` when the cell is missing or unparseable).
#[derive(Debug, Clone, PartialEq)]
pub struct OllamaLoaded {
    pub name: String,
    pub size_bytes: Option<u64>,
}

/// Parses the `ollama ps` table (pure text, no Ollama process spawned, so it
/// runs on Linux CI like the other parsers).
///
/// Columns are aligned with runs of whitespace: a cell may contain single
/// spaces ("51 MB", "100% GPU", "4 minutes from now"), cells are separated
/// by two or more, so the split is by 2+ spaces, never by one. Column
/// positions come from the header row (NAME and SIZE), not from fixed
/// indexes: Ollama has added columns before (CONTEXT) and may add more.
/// Without a recognizable header there is no table to parse and the result is
/// empty; broken or nameless rows are skipped, never fatal, and a row whose
/// SIZE does not parse still yields the model with `size_bytes: None`.
pub fn parse_ollama_ps(text: &str) -> Vec<OllamaLoaded> {
    let Ok(col_sep) = Regex::new(r"\s{2,}") else { return Vec::new() };
    let mut out = Vec::new();
    let mut name_idx = None;
    let mut size_idx = None;
    for line in text.lines() {
        // Sin trim de la linea: las filas arrancan en la columna 0, y recortar
        // espacios iniciales correria las celdas a la izquierda (una fila sin
        // nombre se leeria como si el digest fuera el nombre). Cada celda si
        // se recorta para comparar el encabezado.
        let cells: Vec<&str> = col_sep.split(line).collect();
        let header_name = cells.iter().position(|cell| cell.trim() == "NAME");
        let header_size = cells.iter().position(|cell| cell.trim() == "SIZE");
        if let (Some(n), Some(s)) = (header_name, header_size) {
            // Header row: fix the positions of the columns that matter.
            name_idx = Some(n);
            size_idx = Some(s);
            continue;
        }
        let (Some(n), Some(s)) = (name_idx, size_idx) else { continue };
        let name = cells.get(n).map(|cell| cell.trim()).unwrap_or("");
        if name.is_empty() || name == "NAME" {
            continue;
        }
        // A row shorter than the table's columns is broken data: a shifted or
        // truncated row would misattribute cells, so it is skipped whole.
        // (A present-but-unparseable SIZE still yields the model with
        // `size_bytes: None`; dropping it whole would hide a loaded model.)
        if cells.len() <= s {
            continue;
        }
        out.push(OllamaLoaded {
            name: name.to_string(),
            size_bytes: parse_ollama_size(cells[s]),
        });
    }
    out
}

/// Bytes of one SIZE cell of `ollama ps` ("51 MB", "1.2 GB", "1048576").
/// Ollama humanizes with decimal units — the API reports 51_652_852 bytes and
/// the table says "51 MB" (51.65 truncated) — so KB/MB/GB/TB are powers of a
/// thousand and KiB/MiB/GiB/TiB powers of two. No unit means bytes; anything
/// that does not start with a number is not a size and yields `None`.
fn parse_ollama_size(cell: &str) -> Option<u64> {
    let cell = cell.trim();
    let digits = cell
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .count();
    if digits == 0 {
        return None;
    }
    let value: f64 = cell[..digits].parse().ok()?;
    let factor = match cell[digits..].trim().to_ascii_uppercase().as_str() {
        "" | "B" => 1.0,
        "K" | "KB" => 1e3,
        "M" | "MB" => 1e6,
        "G" | "GB" => 1e9,
        "T" | "TB" => 1e12,
        "KIB" => 1024.0,
        "MIB" => 1024.0 * 1024.0,
        "GIB" => 1024.0 * 1024.0 * 1024.0,
        "TIB" => 1024.0 * 1024.0 * 1024.0 * 1024.0,
        _ => return None,
    };
    Some((value * factor) as u64)
}

/// Models loaded in Ollama, from `ollama ps` output, added to the same
/// `models` list the argv-detected servers feed. The text is captured by
/// executing the `ollama` binary (PATH lookup, no fixed install path and no
/// HTTP client): the project resolves everything by running system tools, and
/// a network dependency is not justified here. Missing binary or empty output
/// means this detection contributes nothing; it can never fail the snapshot.
///
/// Field decisions, all deliberate:
///
/// * `alias` and `model` are the table's NAME: the identifier the user knows
///   the model by (`smollm:135m`); there is no better name to derive.
/// * `rss_gib` carries the SIZE column. It is the number OLLAMA REPORTS as
///   the loaded model's footprint (weights plus KV cache, split between VRAM
///   and RAM per the PROCESSOR column); it is NOT the RSS of any process —
///   `ollama ps` has no pid column, so the runner's real RSS cannot be
///   attributed to one model. Ollama humanizes in decimal units, and the
///   conversion to GiB keeps that rounding. When the cell is missing or
///   unparseable the model still shows, with 0.0 standing for "Ollama did
///   not report a parseable SIZE" (the field is not an `Option`).
/// * `pid` is 0 = unknown: `ollama ps` reports no pid, and pairing a NAME
///   with one of the internal `llama-server` children would be guessing (the
///   argv blob does not derive to the NAME; several models share one daemon).
/// * `cpu` is 0.0 for the same reason: `Model.cpu` is not an `Option`, and
///   here it means "not measurable", never a measured zero.
/// * `port` is the listening socket of the `ollama serve` daemon found in the
///   same ps snapshot, cross-referenced with `lsof` via `listen` — the same
///   daemon that serves the models. No daemon visible, no invented port.
/// * `gtt_gib`/`vram_gib` stay `None`: the already-decided macOS rule (no
///   per-process GPU memory API; `None` is "not measurable", never zero).
pub fn detect_ollama_models(
    rows: &[Process],
    ollama_ps_text: Option<&str>,
    listen: &HashMap<i64, u16>,
) -> Vec<Model> {
    let Some(text) = ollama_ps_text else {
        return Vec::new();
    };
    let port = ollama_serve_pids(rows)
        .iter()
        .find_map(|pid| listen.get(pid).copied())
        .map(i64::from);
    parse_ollama_ps(text)
        .into_iter()
        .map(|loaded| Model {
            alias: loaded.name.clone(),
            pid: 0,
            port,
            model: loaded.name,
            rss_gib: loaded.size_bytes.unwrap_or(0) as f64 / GIB,
            cpu: 0.0,
            gtt_gib: None,
            vram_gib: None,
        })
        .collect()
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

/// Full snapshot: memory, GPU, process table and locally served models.
///
/// The process groups come from the shared `config.toml` (`[[watch]]`
/// blocks), matched against the same `ps` rows that feed model detection —
/// those carry the full argv, which the anchored patterns need; a missing or
/// broken config falls back to the five usual groups.
#[cfg(target_os = "macos")]
pub fn collect_stats() -> Stats {
    let rows = collect_process_args();
    let listen = collect_listen_ports();
    // `ollama ps` via PATH, never a fixed path: the binary lives in
    // /opt/homebrew/bin on Apple Silicon, /usr/local/bin on Intel and
    // anywhere else for source installs. Without Ollama installed `run`
    // returns None and this detection simply contributes nothing.
    let ollama_ps = run(&["ollama", "ps"]);
    let mut models = detect_models(&rows, &listen);
    models.extend(detect_ollama_models(&rows, ollama_ps.as_deref(), &listen));
    Stats {
        memory: collect_memory().unwrap_or(Memory {
            total_gib: 0.0,
            used_gib: 0.0,
            available_gib: 0.0,
            swap_total_gib: 0.0,
            swap_used_gib: 0.0,
        }),
        models,
        gpu: collect_gpu(),
        groups: group_by(&rows, &crate::config::load_watch()),
        processes: collect_processes(),
    }
}

/// `ps -ww -axo pid=,ppid=,uid=,rss=,%cpu=,args=` rows: the ones whose argv
/// the watch patterns are matched against.
#[cfg(target_os = "macos")]
fn collect_process_args() -> Vec<Process> {
    run(&[
        "/bin/ps",
        "-ww",
        "-axo",
        "pid=,ppid=,uid=,rss=,%cpu=,args=",
    ])
    .map(|text| parse_ps_args(&text))
    .unwrap_or_default()
}

/// pid -> listening port, read from `lsof` at capture time.
#[cfg(target_os = "macos")]
fn collect_listen_ports() -> HashMap<i64, u16> {
    run(&["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        .map(|text| listen_ports(&text))
        .unwrap_or_default()
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
    fn agrupar_usa_los_patrones_regex_del_config() {
        let rows = parse_ps(" 1 0 501 1024 10.0 firefox\n 2 0 501 2048 5.0 Google Chrome\n 3 0 501 4096 1.0 ollama\n");
        let watch = vec![
            entry("navegadores", &["^firefox$", "[Cc]hrome"], true),
            entry("local", &["ollama"], true),
        ];
        let groups = group_by(&rows, &watch);
        assert_eq!(groups["navegadores"].pids, vec![1, 2]);
        assert_eq!(groups["local"].cpu, 1.0);
        assert!(!groups.contains_key("modelos"), "un patron sin coincidencia no crea el grupo");
    }

    #[test]
    fn entrada_invisible_no_arma_grupo_y_sus_procesos_caen_al_fallback() {
        let rows = parse_ps(
            " 1 0 501 1024 10.0 firefox\n 2 1 0 2048 5.0 systemd\n 3 1 501 4096 1.0 otrocosa\n",
        );
        let watch = vec![
            entry("firefox", &["^firefox$"], false),
            entry("system", &[], true),
            entry("other", &[], true),
        ];
        let groups = group_by(&rows, &watch);
        assert!(!groups.contains_key("firefox"), "una entrada oculta no aparece");
        assert_eq!(groups["system"].pids, vec![2], "system sigue siendo la regla fija de uid 0");
        assert_eq!(
            groups["other"].pids,
            vec![1, 3],
            "lo que el grupo oculto hubiera tomado cae al resto"
        );
    }

    #[test]
    fn regex_rota_se_ignora_sin_bajar_al_panic() {
        let rows = parse_ps(" 1 0 501 1024 10.0 firefox\n");
        let watch = vec![
            entry("roto", &["("], true),
            entry("firefox", &["^firefox$"], true),
        ];
        let groups = group_by(&rows, &watch);
        assert!(groups["roto"].pids.is_empty(), "un patron roto no matchea nada");
        assert_eq!(groups["firefox"].pids, vec![1], "los demas patrones siguen vivos");
    }

    #[test]
    fn los_defaults_reproducen_el_camino_de_linux() {
        let rows = parse_ps(
            " 1 0 0 1024 1.0 systemd\n 2 1 501 2048 2.0 pi\n 3 1 501 4096 3.0 firefox\n 4 1 501 8192 4.0 halliballo\n",
        );
        let groups = group_by(&rows, &crate::config::default_watch());
        assert_eq!(groups["pi"].pids, vec![2]);
        assert_eq!(groups["firefox"].pids, vec![3]);
        assert_eq!(groups["system"].pids, vec![1]);
        assert_eq!(groups["other"].pids, vec![4]);
    }

    #[test]
    fn un_proceso_cae_en_un_solo_grupo_el_primero_del_config() {
        let rows = parse_ps(" 1 0 501 1024 1.0 firefox\n");
        let watch = vec![
            entry("navegadores", &["firefox"], true),
            entry("todos", &["fire"], true),
        ];
        let groups = group_by(&rows, &watch);
        assert_eq!(groups["navegadores"].pids, vec![1]);
        assert!(groups["todos"].pids.is_empty(), "el primer patron que coincide se queda el proceso");
    }

    /// Construye una entrada [[watch]] como las que produce `config.rs`.
    fn entry(name: &str, patterns: &[&str], visible: bool) -> WatchEntry {
        WatchEntry {
            name: name.into(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            icon: String::new(),
            visible,
        }
    }

    #[test]
    fn lsof_se_traduce_al_formato_ss() {
        let lsof = "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n\
                    ollama  4242 user   14u  IPv4 0xa111      0t0  TCP 127.0.0.1:11434 (LISTEN)\n\
                    Chrome  9313 user   33u  IPv6 0xb222      0t0  TCP [::1]:9222 (LISTEN)\n";
        let ss = lsof_to_ss(lsof);
        assert!(ss.contains("127.0.0.1:11434"), "{ss}");
        assert!(ss.contains("pid=4242"), "{ss}");
        assert_eq!(ss.lines().count(), 3, "una fila por socket, mas la cabecera");
        // El puente alimenta al parser portable de ss: los pids deben sobrevivir
        // (con comillas, como en una linea ss real) para poder cruzar modelos
        // con sockets en escucha.
        let rows = parse_ss_output(&ss);
        let ollama = rows.iter().find(|r| r.port == 11434).expect("socket de ollama");
        assert_eq!(ollama.pid, 4242, "parse_ss_output debe recuperar el pid del puente lsof");
        let chrome = rows.iter().find(|r| r.port == 9222).expect("socket de chrome");
        assert_eq!(chrome.pid, 9313);
    }

    // -- deteccion de modelos locales (argv de ps) -----------------------------

    // Layout real del comando de produccion `ps -ww -axo pid=,ppid=,uid=,rss=,
    // %cpu=,args=`: cinco columnas numericas y despues args como resto libre de
    // la linea (puede contener espacios). NUNCA se pide `comm=` junto a `args=`:
    // macOS lo trunca a MAXCOMLEN (16 chars) exactamente cuando tambien se pide
    // args, como prueba la captura real (seccion ps-args de
    // tests/fixtures/macos-capture.txt).

    #[test]
    fn llama_server_con_alias_y_puerto_de_argv() {
        let ps = " 4200     1   501 8388608  12.5 /opt/homebrew/bin/llama-server --host 127.0.0.1 --port 11002 --alias abito-gpt -m /Users/runner/models/qwen2.5-7b-instruct-q4_k_m.gguf\n";
        let models = detect_models(&parse_ps_args(ps), &HashMap::new());
        assert_eq!(models.len(), 1);
        let m = &models[0];
        assert_eq!(m.alias, "abito-gpt", "el --alias manda sobre el basename");
        assert_eq!(m.model, "/Users/runner/models/qwen2.5-7b-instruct-q4_k_m.gguf");
        assert_eq!(m.port, Some(11002));
        assert_eq!(m.pid, 4200);
        assert!((m.rss_gib - 8.0).abs() < 1e-9, "rss_gib={}", m.rss_gib);
        assert!((m.cpu - 12.5).abs() < 1e-9, "cpu={}", m.cpu);
        assert_eq!(m.gtt_gib, None, "macOS no mide GPU por proceso: None, nunca 0.0");
        assert_eq!(m.vram_gib, None, "macOS no mide GPU por proceso: None, nunca 0.0");
    }

    #[test]
    fn vllm_serve_con_puerto_de_argv() {
        let ps = " 4300     1   501 16777216   3.2 /usr/local/bin/vllm serve meta-llama/Llama-3-8B-Instruct --port 8000\n";
        let models = detect_models(&parse_ps_args(ps), &HashMap::new());
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].alias, "Llama-3-8B-Instruct", "sin --alias, el basename del modelo");
        assert_eq!(models[0].model, "meta-llama/Llama-3-8B-Instruct");
        assert_eq!(models[0].port, Some(8000));
    }

    #[test]
    fn vllm_por_modulo_python_no_depende_del_binario() {
        let ps = " 4400     1   501  8388608   1.1 python -m vllm.entrypoints.openai.api_server --model mistralai/Mistral-7B-v0.1 --port 8001\n";
        let models = detect_models(&parse_ps_args(ps), &HashMap::new());
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].alias, "Mistral-7B-v0.1");
        assert_eq!(models[0].model, "mistralai/Mistral-7B-v0.1");
        assert_eq!(models[0].port, Some(8001));
    }

    #[test]
    fn mlx_lm_por_modulo_y_flag_de_puerto_con_igual() {
        let ps = " 4700     1   501  2097152   0.7 python -m mlx_lm.server --model /models/tiny-llama-1.1b-chat.gguf --port=8080\n";
        let models = detect_models(&parse_ps_args(ps), &HashMap::new());
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].alias, "tiny-llama-1.1b-chat", "basename sin .gguf");
        assert_eq!(models[0].port, Some(8080), "--port=8080 con igual tambien se lee");
    }

    #[test]
    fn un_navegador_no_es_un_modelo() {
        let ps = " 9313     1   501 4194304   0.0 /Applications/Safari.app/Contents/MacOS/Safari\n\
                  9314     1   501  524288   0.1 /usr/libexec/UserEventAgent (System)\n";
        let rows = parse_ps_args(ps);
        assert_eq!(rows.len(), 2);
        assert!(detect_models(&rows, &HashMap::new()).is_empty());
    }

    #[test]
    fn args_con_espacios_no_rompen_el_resto_libre() {
        let ps = " 9313     1   501 4194304   0.0 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --type=renderer\n";
        let rows = parse_ps_args(ps);
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0].args,
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --type=renderer",
            "args es el resto libre completo de la linea, espacios incluidos"
        );
        assert!(detect_models(&rows, &HashMap::new()).is_empty());
    }

    #[test]
    fn puerto_faltante_sale_del_cruce_con_lsof() {
        let ps = " 4600     1   501 10485760   2.2 /opt/homebrew/bin/llama-server -m /Users/runner/models/mixtral-8x7b-instruct.gguf\n\
                  4601     1   501 10485760   2.2 /opt/homebrew/bin/llama-server -m /Users/runner/models/otro-servidor.gguf\n";
        let lsof = "COMMAND        PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n\
                    llama-server  4600 runner   14u  IPv4 0xa111      0t0  TCP 127.0.0.1:11434 (LISTEN)\n\
                    llama-server  4600 runner   15u  IPv6 0xb222      0t0  TCP [::1]:11434 (LISTEN)\n";
        let listen = listen_ports(lsof);
        let models = detect_models(&parse_ps_args(ps), &listen);
        assert_eq!(models.len(), 2);
        assert_eq!(models[0].alias, "mixtral-8x7b-instruct", "basename sin .gguf");
        assert_eq!(models[0].port, Some(11434), "el puerto viene del socket en escucha");
        assert_eq!(models[1].port, None, "sin puerto en argv ni socket: None, nunca 0");
    }

    // -- falso positivo: el runner interno de Ollama NO es llama.cpp --------

    /// Arbol de procesos real capturado en el runner macOS (b54a80c):
    /// `ollama serve` es el padre y su hijo `llama-server` carga un modelo
    /// desde el almacen de blobs de Ollama. El blob no derivaba a un alias
    /// con nombre y mostraba un modelo fantasma al lado del real.
    const OLLAMA_TREE: &str = concat!(
        "15442 14974 501 10485760  0.5 ollama serve\n",
        "16327 15442 501 20971520  1.2 /opt/homebrew/Cellar/ollama/0.33.0/libexec/lib/ollama/llama-server --model /Users/runner/.ollama/models/blobs/sha256-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n",
    );

    #[test]
    fn el_runner_interno_de_ollama_no_es_un_servidor_llama_cpp() {
        let models = detect_models(&parse_ps_args(OLLAMA_TREE), &HashMap::new());
        assert!(
            models.is_empty(),
            "el hijo de `ollama serve` se detecto como llama.cpp propio: {:?}",
            models.iter().map(|m| &m.alias).collect::<Vec<_>>()
        );
    }

    #[test]
    fn el_bloque_por_ruta_de_blobs_no_depende_del_padre() {
        // Mismo hijo sin el daemon en el snapshot (padre no capturado):
        // la ruta del --model dentro del almacen de blobs de Ollama basta.
        let ps = " 16327     1   501 20971520  1.2 /opt/homebrew/bin/llama-server --model /Users/runner/.ollama/models/blobs/sha256-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n";
        assert!(detect_models(&parse_ps_args(ps), &HashMap::new()).is_empty());
    }

    #[test]
    fn ollama_no_tapa_al_llama_server_legitimo() {
        // Suprimir el falso positivo no puede costar el verdadero: un
        // llama-server propio (hijo de launchd, --model a un .gguf real) en la
        // misma maquina sigue dando su modelo.
        let mut ps = String::from(OLLAMA_TREE);
        ps.push_str(" 4200     1   501 8388608  12.5 /opt/homebrew/bin/llama-server --port 11002 --alias abito-gpt -m /models/qwen2.5-7b-instruct-q4_k_m.gguf\n");
        let models = detect_models(&parse_ps_args(&ps), &HashMap::new());
        assert_eq!(models.len(), 1, "solo el llama-server legitimo, sin fantasma");
        assert_eq!(models[0].alias, "abito-gpt");
    }

    // -- Ollama (`ollama ps`) --------------------------------------------------

    /// Tabla literal de `ollama ps` capturada en el runner macOS con
    /// `smollm:135m` cargado (evidencia real, b54a80c).
    const OLLAMA_PS_REAL: &str = concat!(
        "NAME           ID              SIZE     PROCESSOR    CONTEXT    UNTIL\n",
        "smollm:135m    b0b2a4617438    51 MB    100% GPU     2048       4 minutes from now\n",
    );

    #[test]
    fn ollama_ps_captura_real_da_el_modelo_cargado() {
        let parsed = parse_ollama_ps(OLLAMA_PS_REAL);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "smollm:135m");
        // "51 MB" en unidades decimales: la API reporta 51_652_852 bytes y
        // Ollama los humaniza como "51 MB" (51.65 truncado).
        assert_eq!(parsed[0].size_bytes, Some(51_000_000));
    }

    #[test]
    fn ollama_ps_sin_modelos_cargados_no_produce_nada() {
        // Maquina sin modelos cargados: solo el encabezado, ni un modelo ni
        // un error. Tampoco hay tabla sin encabezado reconocible.
        let header_only = "NAME           ID              SIZE     PROCESSOR    CONTEXT    UNTIL\n";
        assert!(parse_ollama_ps(header_only).is_empty());
        assert!(parse_ollama_ps("").is_empty());
        assert!(parse_ollama_ps("texto que no es la tabla\n").is_empty());
    }

    #[test]
    fn ollama_ps_filas_rotas_o_sin_size_se_ignoran() {
        let text = concat!(
            "NAME           ID              SIZE     PROCESSOR    CONTEXT    UNTIL\n",
            "m1    b0b2a4617438    10 MB    100% GPU    2048    soon\n",
            "m2    solo-dos-columnas\n",
            "    b0b2a4617438    10 MB    fila sin nombre\n",
            "m3    b0b2a4617438    NaN MB    100% GPU    2048    soon\n",
        );
        let parsed = parse_ollama_ps(text);
        assert_eq!(parsed.len(), 2, "las filas sin nombre se descartan");
        assert_eq!(parsed[0].name, "m1");
        assert_eq!(parsed[0].size_bytes, Some(10_000_000));
        assert_eq!(parsed[1].name, "m3");
        assert_eq!(
            parsed[1].size_bytes, None,
            "SIZE no parseable: modelo sin tamano, nunca un numero inventado"
        );
    }

    #[test]
    fn ollama_ps_unidades_de_tamano() {
        // Ollama humaniza en decimales (51652852 bytes -> "51 MB").
        assert_eq!(parse_ollama_size("51 MB"), Some(51_000_000));
        assert_eq!(parse_ollama_size("512MB"), Some(512_000_000));
        assert_eq!(parse_ollama_size("1.2 GB"), Some(1_200_000_000));
        assert_eq!(parse_ollama_size("1048576"), Some(1_048_576), "sin unidad: bytes");
        assert_eq!(parse_ollama_size("1 KiB"), Some(1024));
        assert_eq!(parse_ollama_size("pronto"), None);
        assert_eq!(parse_ollama_size(""), None);
    }

    #[test]
    fn ollama_entra_a_la_misma_lista_models() {
        // El arbol real mas la tabla real: un solo modelo, el de Ollama, con
        // las reglas de campos decididas.
        let rows = parse_ps_args(OLLAMA_TREE);
        let models = detect_ollama_models(&rows, Some(OLLAMA_PS_REAL), &HashMap::new());
        assert_eq!(models.len(), 1);
        let m = &models[0];
        assert_eq!(m.alias, "smollm:135m", "alias = NAME");
        assert_eq!(m.model, "smollm:135m", "model = NAME");
        assert_eq!(m.pid, 0, "ollama ps no da pid: 0 = desconocido");
        assert_eq!(m.port, None, "sin daemon visible en el snapshot: sin puerto");
        assert!((m.rss_gib - 51_000_000f64 / GIB).abs() < 1e-9, "rss_gib={}", m.rss_gib);
        assert_eq!(m.gtt_gib, None, "regla macOS: None, nunca 0.0");
        assert_eq!(m.vram_gib, None, "regla macOS: None, nunca 0.0");
    }

    #[test]
    fn ollama_ps_ausente_no_aporta_nada() {
        // Sin binario `ollama` la captura es None: la deteccion aporta nada
        // y no puede fallar.
        let rows = parse_ps_args(OLLAMA_TREE);
        assert!(detect_ollama_models(&rows, None, &HashMap::new()).is_empty());
    }

    #[test]
    fn ollama_toma_el_puerto_del_serve_del_cruce_con_lsof() {
        // El socket que sirve los modelos es el del demonio `ollama serve`,
        // cruzado con lsof igual que los demas servidores.
        let ps = "15442 14974 501 10485760  0.5 /opt/homebrew/bin/ollama serve\n";
        let lsof = "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n\
                    ollama  15442 user   14u  IPv4 0xa111      0t0  TCP 127.0.0.1:11434 (LISTEN)\n";
        let listen = listen_ports(lsof);
        let models = detect_ollama_models(&parse_ps_args(ps), Some(OLLAMA_PS_REAL), &listen);
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].port, Some(11434));
    }

    #[test]
    fn la_captura_real_ps_args_no_tiene_modelos() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("macos-capture.txt");
        let text = std::fs::read_to_string(&path).expect("la captura comprometida debe leerse");
        let section = fixture_section(&text, "ps-args");
        let rows = parse_ps_args(&section);
        assert!(!rows.is_empty(), "la captura real tiene procesos");
        assert!(
            detect_models(&rows, &HashMap::new()).is_empty(),
            "la captura real del runner no corre servidores de modelos"
        );
    }

    /// Extrae una seccion `===== BEGIN name =====` de la captura comprometida.
    fn fixture_section(text: &str, name: &str) -> String {
        let marker = format!("===== BEGIN {name} =====");
        let mut out = String::new();
        let mut inside = false;
        for line in text.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with("===== BEGIN ") {
                inside = trimmed == marker;
                continue;
            }
            if inside {
                out.push_str(line);
                out.push('\n');
            }
        }
        out
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
