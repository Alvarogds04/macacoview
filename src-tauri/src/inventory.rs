//! Inventory of the local models installed on this machine.
//!
//! Two sources feed it, merged into one list for the UI:
//!
//! * **Ollama**: `GET http://127.0.0.1:11434/api/tags` — the same endpoint the
//!   `ollama` CLI is a client of, so no binary has to be on `PATH`. Only
//!   `std::net::TcpStream` is used; a custom host is explicitly out of scope,
//!   so `OLLAMA_HOST` is deliberately not read.
//! * **Disk**: every directory configured under `model_dirs` in the shared
//!   `config.toml`, scanned for `*.gguf` files. `parameters` and
//!   `quantization` are derived from the file name, not measured: they are
//!   hints, and a badly named file yields a wrong hint — never a measurement.
//!
//! The whole inventory is recomputed at most once per [`INVENTORY_TTL_SECS`]:
//! the sampling loop ticks every second, and both a HTTP request and a
//! directory walk are far too expensive for that cadence. On a refresh that
//! can observe nothing at all (no Ollama endpoint reachable, no usable disk
//! entries) the previous cached list is kept, the same policy the token loop
//! uses — a transient failure must not blank a section that was showing real
//! numbers.

use std::collections::HashSet;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

/// One installed model: present on the machine (Ollama's catalog or a GGUF
/// file on disk), which is a different claim from the `Model` list of models
/// currently loaded in memory.
///
/// `parameters` and `quantization` from the disk source are hints read from
/// the file name, never measurements; Ollama reports its own for real.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq)]
pub struct InstalledModel {
    /// `llama3.1:8b` (Ollama) or `qwen2.5-7b-Q4_K_M.gguf` (disk, relative to
    /// the configured directory).
    pub name: String,
    /// `"ollama"` or `"disk"`.
    pub source: String,
    /// File size in bytes, when it is known.
    pub size_bytes: Option<u64>,
    /// `"8.0B"` from Ollama, or a hint parsed from the file name.
    pub parameters: Option<String>,
    /// `"Q4_K_M"` from Ollama, or a hint parsed from the file name.
    pub quantization: Option<String>,
}

/// How often the inventory is recomputed, in seconds. The sampling loop runs
/// every second; nothing here may.
pub const INVENTORY_TTL_SECS: u64 = 60;

/// Ollama's default API endpoint. Fixed on purpose: no DNS lookup, no
/// environment variable, no custom host.
const OLLAMA_ADDR: SocketAddr = SocketAddr::new(
    std::net::IpAddr::V4(std::net::Ipv4Addr::new(127, 0, 0, 1)),
    11434,
);

/// Every blocking operation is bounded so a hung Ollama can never stall the
/// sampling loop: connect at most 300 ms, read at most 500 ms.
const OLLAMA_CONNECT_TIMEOUT: Duration = Duration::from_millis(300);
const OLLAMA_READ_TIMEOUT: Duration = Duration::from_millis(500);

/// Disk-walk budget: at most 4 levels of subdirectories under each configured
/// directory, and at most 500 matching files in total before the walk stops.
/// Only `*.gguf` files consume the budget, so the cap bounds the list, not the
/// time: a configured directory full of unrelated files is still walked whole
/// (the depth cap is what bounds that, and the 60 s TTL is what bounds how
/// often it happens).
pub const MAX_WALK_DEPTH: usize = 4;
pub const MAX_WALK_FILES: usize = 500;

const QUANT_PATTERN: &str =
    r"(?i)(?:^|[^A-Za-z0-9])((?:IQ|Q)\d+(?:_[A-Z0-9]+)*|BF16|F16|F32)(?:$|[^A-Za-z0-9])";
const PARAMS_PATTERN: &str = r"(?i)(?:^|[^A-Za-z0-9])((?:\d+(?:\.\d+)?)B)(?:$|[^A-Za-z0-9])";

// ---------------------------------------------------------------------------
// Ollama over HTTP
// ---------------------------------------------------------------------------

/// Raw response of `GET /api/tags` from Ollama's default endpoint, or `None`
/// when the daemon is unreachable or too slow. A machine without Ollama is a
/// normal state, not an error: `None` simply contributes no entries.
fn fetch_ollama_tags_json() -> Option<String> {
    use std::io::{Read, Write};

    let mut stream = TcpStream::connect_timeout(&OLLAMA_ADDR, OLLAMA_CONNECT_TIMEOUT).ok()?;
    stream.set_read_timeout(Some(OLLAMA_READ_TIMEOUT)).ok()?;
    stream.set_write_timeout(Some(OLLAMA_READ_TIMEOUT)).ok()?;

    // `Connection: close` so the server ends the response and `read_to_end`
    // terminates; the read timeout bounds a server that stalls anyway.
    let request = format!(
        "GET /api/tags HTTP/1.1\r\n\
         Host: {}\r\n\
         Accept: application/json\r\n\
         Connection: close\r\n\
         \r\n",
        OLLAMA_ADDR,
    );
    stream.write_all(request.as_bytes()).ok()?;

    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).ok()?;
    String::from_utf8(raw).ok()
}

/// Body of an HTTP response: everything after the header block. Only a 2xx
/// status line is accepted; with no separator (not even the end of the
/// headers) there is no body to speak of.
pub fn http_body(response: &str) -> Option<&str> {
    let status = response.lines().next()?;
    if !status.contains(" 200 ") && !status.ends_with(" 200") {
        return None;
    }
    let at = response
        .find("\r\n\r\n")
        .map(|i| i + 4)
        .or_else(|| response.find("\n\n").map(|i| i + 2))?;
    Some(&response[at..])
}

/// One entry of the `models` array of `/api/tags` into an [`InstalledModel`].
/// An entry without a usable `name` is broken data and is skipped whole; a
/// missing `details` object only costs the optional fields.
fn parse_tag_entry(entry: &serde_json::Value) -> Option<InstalledModel> {
    let name = entry.get("name")?.as_str()?.trim();
    if name.is_empty() {
        return None;
    }
    let details = entry.get("details");
    Some(InstalledModel {
        name: name.to_string(),
        source: "ollama".to_string(),
        size_bytes: entry.get("size").and_then(serde_json::Value::as_u64),
        parameters: details
            .and_then(|d| d.get("parameter_size"))
            .and_then(serde_json::Value::as_str)
            .map(str::to_string),
        quantization: details
            .and_then(|d| d.get("quantization_level"))
            .and_then(serde_json::Value::as_str)
            .map(str::to_string),
    })
}

/// Parses the JSON body of `/api/tags` (pure function: testable on any OS,
/// no Ollama needed). Not JSON, no `models` array, or a non-array `models`
/// all mean "nothing to add"; broken entries are skipped one by one.
pub fn parse_tags_json(body: &str) -> Vec<InstalledModel> {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(body) else {
        return Vec::new();
    };
    let Some(models) = value.get("models").and_then(serde_json::Value::as_array) else {
        return Vec::new();
    };
    models.iter().filter_map(parse_tag_entry).collect()
}

// ---------------------------------------------------------------------------
// Name heuristics
// ---------------------------------------------------------------------------

/// First whole token of the file name that looks like a quantization tag
/// (`Q4_K_M`, `Q8_0`, `IQ3_XXS`, `F16`, `BF16`, `F32`, case-insensitive).
///
/// These come from the NAME, not from the file: a badly named file yields a
/// wrong hint, so the value is a hint, never a measurement. Tokens must be
/// delimited — never part of a longer alphanumeric run — so `q4km.gguf` and
/// `ggmlQ4_0.gguf` match nothing at all. The token is returned exactly as it is
/// written in the name, case included, so it can always be traced back to the
/// file it came from.
pub fn quantization_from_name(name: &str) -> Option<String> {
    quantization_regex()
        .and_then(|re| re.captures(name))
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_string())
}

/// First whole token of the file name that looks like a parameter count
/// (`8B`, `7.6B`, case-insensitive), unit included and returned as written,
/// like [`quantization_from_name`]. Same caveats: a hint from the name, not a
/// measurement, and always delimited — `qwen2.5-7b` matches `7b`, never the
/// `2.5` inside the `qwen2.5` run.
pub fn parameters_from_name(name: &str) -> Option<String> {
    parameters_regex()
        .and_then(|re| re.captures(name))
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_string())
}

/// The fixed quantization pattern, compiled once. If a compile-time constant
/// ever failed to compile the heuristics degrade to no hints; the tests pin
/// the patterns to real inputs so that would turn red, not hide.
fn quantization_regex() -> Option<&'static regex::Regex> {
    static RE: OnceLock<Option<regex::Regex>> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(QUANT_PATTERN).ok()).as_ref()
}

/// The fixed parameter-count pattern, compiled once. See [`quantization_regex`].
fn parameters_regex() -> Option<&'static regex::Regex> {
    static RE: OnceLock<Option<regex::Regex>> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(PARAMS_PATTERN).ok()).as_ref()
}

// ---------------------------------------------------------------------------
// Disk walk
// ---------------------------------------------------------------------------

/// Walks each configured directory for `*.gguf` files (extension matched
/// case-insensitively), at most [`MAX_WALK_DEPTH`] levels deep per directory
/// and [`MAX_WALK_FILES`] matching files in total, then stops. `name` is the path
/// relative to the configured directory; unreadable directories are skipped.
pub fn walk_gguf_dirs(dirs: &[PathBuf]) -> Vec<InstalledModel> {
    walk_gguf_dirs_with_budget(dirs, MAX_WALK_FILES)
}

/// Same walk with an explicit file budget, so the cap is testable without
/// creating 500 files.
fn walk_gguf_dirs_with_budget(dirs: &[PathBuf], budget: usize) -> Vec<InstalledModel> {
    let mut out = Vec::new();
    let mut remaining = budget;
    for dir in dirs {
        if remaining == 0 {
            break; // total cap reached: stop everything
        }
        walk_dir(dir, dir, 0, &mut out, &mut remaining);
    }
    out
}

fn walk_dir(
    root: &Path,
    dir: &Path,
    depth: usize,
    out: &mut Vec<InstalledModel>,
    remaining: &mut usize,
) {
    if *remaining == 0 || depth > MAX_WALK_DEPTH {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return; // unreadable directory: skip, never fatal
    };
    for entry in entries.flatten() {
        if *remaining == 0 {
            return;
        }
        let path = entry.path();
        if path.is_dir() {
            walk_dir(root, &path, depth + 1, out, remaining);
        } else if path.is_file() && is_gguf(&path) {
            let name = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();
            let size_bytes = entry.metadata().ok().map(|meta| meta.len());
            out.push(InstalledModel {
                name,
                source: "disk".to_string(),
                size_bytes,
                parameters: parameters_from_name(&path.file_name().unwrap_or_default().to_string_lossy()),
                quantization: quantization_from_name(
                    &path.file_name().unwrap_or_default().to_string_lossy(),
                ),
            });
            *remaining -= 1;
        }
    }
}

fn is_gguf(path: &Path) -> bool {
    path.extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("gguf"))
}

// ---------------------------------------------------------------------------
// Merge and cache
// ---------------------------------------------------------------------------

/// Merges both sources: Ollama entries first, then disk entries, deduplicated
/// case-insensitively by name (Ollama wins: it knows the model's real size and
/// details) and sorted by name for a stable UI.
pub fn merge_models(ollama: Vec<InstalledModel>, disk: Vec<InstalledModel>) -> Vec<InstalledModel> {
    let mut out: Vec<InstalledModel> = Vec::with_capacity(ollama.len() + disk.len());
    let mut seen: HashSet<String> = HashSet::with_capacity(out.capacity());
    for model in ollama.into_iter().chain(disk) {
        // `insert` returns false exactly when a case-variant of this name is
        // already in: first come (Ollama) wins, the duplicate is dropped.
        if seen.insert(model.name.to_lowercase()) {
            out.push(model);
        }
    }
    // Case-insensitive sort: "B" and "b" models interleave the way a user
    // reads them, instead of every capital landing before every lowercase.
    out.sort_by(|a, b| {
        a.name
            .to_lowercase()
            .cmp(&b.name.to_lowercase())
            .then_with(|| a.name.cmp(&b.name))
    });
    out
}

/// One full refresh of the inventory, or `None` when nothing could be
/// observed at all: no Ollama endpoint answered AND the disk contributed no
/// entries. That combination is indistinguishable from a transient failure
/// (Ollama restarting, a disk briefly unmounted), so the caller keeps the
/// previous cached value instead of blanking the UI section.
fn refresh() -> Option<Vec<InstalledModel>> {
    let ollama = fetch_ollama_tags_json().map(|response| {
        http_body(&response)
            .map(parse_tags_json)
            .unwrap_or_default()
    });
    let disk = walk_gguf_dirs(&crate::config::load_model_dirs());
    match (&ollama, disk.is_empty()) {
        (None, true) => None,
        _ => Some(merge_models(ollama.unwrap_or_default(), disk)),
    }
}

/// Cached inventory: when it was computed, and what it held at that moment.
type CachedInventory = Mutex<Option<(Instant, Vec<InstalledModel>)>>;

/// Cached inventory, shared by every platform collector behind a `OnceLock` so
/// Linux and macOS use one implementation.
fn cache() -> &'static CachedInventory {
    static CACHE: OnceLock<CachedInventory> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(None))
}

/// Installed models for the current tick: cached, refreshed at most once per
/// [`INVENTORY_TTL_SECS`]. On a refresh that observes nothing the previous
/// list is carried forward, exactly like the token loop's policy.
pub fn collect() -> Vec<InstalledModel> {
    let mut guard = cache().lock();
    if let Some((computed_at, models)) = guard.as_ref() {
        if computed_at.elapsed() < Duration::from_secs(INVENTORY_TTL_SECS) {
            return models.clone();
        }
    }
    match refresh() {
        Some(models) => {
            *guard = Some((Instant::now(), models.clone()));
            models
        }
        // Keep the previous value: a transient failure must not blank a
        // section that was showing real numbers. Without a previous value
        // there is nothing to keep, and an empty list is the honest answer.
        None => guard
            .as_ref()
            .map(|(_, models)| models.clone())
            .unwrap_or_default(),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::parse_model_dirs_in;

    /// A temporary directory unique per test, following the project pattern:
    /// no `tempfile` dependency, and each test works only inside its own
    /// uniquely named directory.
    fn temp_dir(tag: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system clock should advance")
            .as_nanos();
        std::env::temp_dir().join(format!("pc-ai-monitor-inv-{tag}-{unique}"))
    }

    /// Creates `dir/<rel>` (with parent directories) with the given contents.
    fn plant_file(dir: &Path, rel: &str, contents: &[u8]) -> PathBuf {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().expect("relative paths always have a parent"))
            .expect("test directory should be creatable");
        std::fs::write(&path, contents).expect("test file should be writable");
        path
    }

    // -- /api/tags parsing --------------------------------------------------

    #[test]
    fn tags_json_parsea_el_formato_real_de_ollama() {
        // Realistic /api/tags body: full details, a model whose details object
        // is missing entirely, and malformed entries (no usable name).
        let body = r#"{
            "models": [
                {
                    "name": "llama3.1:8b",
                    "model": "llama3.1:8b",
                    "modified_at": "2024-08-01T12:00:00.000000000Z",
                    "size": 4920753328,
                    "digest": "365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5f095733d6",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": "llama",
                        "families": ["llama"],
                        "parameter_size": "8.0B",
                        "quantization_level": "Q4_K_M"
                    }
                },
                {
                    "name": "qwen2.5:7b",
                    "model": "qwen2.5:7b",
                    "modified_at": "2024-09-10T08:30:00.000000000Z",
                    "size": 4720553088,
                    "digest": "845dbda0ea48e0a5f2b2f4b3f0e1a7f2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8"
                },
                {
                    "model": "sin-nombre",
                    "size": 12345
                },
                {
                    "name": 42,
                    "size": 1
                },
                {
                    "name": "   ",
                    "size": 1
                }
            ]
        }"#;
        let models = parse_tags_json(body);
        assert_eq!(models.len(), 2, "broken entries are skipped, good ones kept");

        assert_eq!(models[0].name, "llama3.1:8b");
        assert_eq!(models[0].source, "ollama");
        assert_eq!(models[0].size_bytes, Some(4920753328));
        assert_eq!(models[0].parameters.as_deref(), Some("8.0B"));
        assert_eq!(models[0].quantization.as_deref(), Some("Q4_K_M"));

        // No `details` object: the model stays, the optional fields are None.
        assert_eq!(models[1].name, "qwen2.5:7b");
        assert_eq!(models[1].size_bytes, Some(4720553088));
        assert_eq!(models[1].parameters, None);
        assert_eq!(models[1].quantization, None);
    }

    #[test]
    fn tags_json_roto_o_sin_models_devuelve_vacio() {
        assert!(parse_tags_json("esto no es json").is_empty());
        assert!(parse_tags_json("{}").is_empty());
        assert!(parse_tags_json(r#"{"models": "nope"}"#).is_empty());
        assert!(parse_tags_json(r#"{"models": []}"#).is_empty());
        assert!(parse_tags_json("").is_empty());
    }

    #[test]
    fn http_body_extrae_el_cuerpo_y_exige_2xx() {
        let response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"models\":[]}";
        assert_eq!(http_body(response), Some("{\"models\":[]}"));

        // Bare LF headers (a lenient server) still separate.
        let response = "HTTP/1.0 200 OK\nContent-Length: 2\n\n[]";
        assert_eq!(http_body(response), Some("[]"));

        // Anything that is not a 2xx carries no body we would trust.
        assert_eq!(http_body("HTTP/1.1 404 Not Found\r\n\r\nnope"), None);
        // No header/body separator: nothing to speak of.
        assert_eq!(http_body("HTTP/1.1 200 OK"), None);
    }

    // -- name heuristics ----------------------------------------------------

    #[test]
    fn cuantizacion_se_lee_del_nombre_cuando_esta_delimitada() {
        assert_eq!(
            quantization_from_name("qwen2.5-7b-Q4_K_M.gguf").as_deref(),
            Some("Q4_K_M")
        );
        assert_eq!(quantization_from_name("llama-Q8_0.gguf").as_deref(), Some("Q8_0"));
        assert_eq!(
            quantization_from_name("granite-IQ3_XXS.gguf").as_deref(),
            Some("IQ3_XXS")
        );
        assert_eq!(quantization_from_name("model-F16.gguf").as_deref(), Some("F16"));
        assert_eq!(quantization_from_name("model-BF16.gguf").as_deref(), Some("BF16"));
        assert_eq!(quantization_from_name("model-F32.gguf").as_deref(), Some("F32"));
        // Case-insensitive: the token is returned as written in the name.
        assert_eq!(quantization_from_name("model-q5_k_m.gguf").as_deref(), Some("q5_k_m"));
    }

    #[test]
    fn cuantizacion_inexistente_o_no_delimitada_devuelve_none() {
        // The canonical negative: a bare name carries no hint at all.
        assert_eq!(quantization_from_name("model.gguf"), None);
        assert_eq!(quantization_from_name("mistral-7b-instruct.gguf"), None);
        // Delimited-only rule: glued to letters it is part of a longer run.
        assert_eq!(quantization_from_name("q4km.gguf"), None);
        assert_eq!(quantization_from_name("ggmlQ4_0.gguf"), None);
        // Numbers that are not quantization tags.
        assert_eq!(quantization_from_name("Llama-3.2.gguf"), None);
    }

    #[test]
    fn parametros_se_leen_del_nombre_cuando_estan_delimitados() {
        // The unit travels with the count, and the case stays as written.
        assert_eq!(parameters_from_name("qwen2.5-7b-Q4_K_M.gguf").as_deref(), Some("7b"));
        assert_eq!(parameters_from_name("model-8B.gguf").as_deref(), Some("8B"));
        assert_eq!(parameters_from_name("model-7.6B.gguf").as_deref(), Some("7.6B"));
        assert_eq!(parameters_from_name("llama3.1:8b").as_deref(), Some("8b"));
        // Not the "2.5" inside the qwen2.5 alphanumeric run.
        assert_eq!(parameters_from_name("qwen2.5.gguf"), None);
        // The canonical negative.
        assert_eq!(parameters_from_name("model.gguf"), None);
        // "1B" glued after a letter is not a parameter count.
        assert_eq!(parameters_from_name("A1B2C3.gguf"), None);
    }

    // -- merge / dedup ------------------------------------------------------

    #[test]
    fn merge_deduplica_sin_importar_mayusculas_y_ollama_gana() {
        let ollama = vec![InstalledModel {
            name: "LLAMA3:8B".into(),
            source: "ollama".into(),
            size_bytes: Some(4920753328),
            parameters: Some("8.0B".into()),
            quantization: Some("Q4_K_M".into()),
        }];
        let disk = vec![
            InstalledModel {
                name: "llama3:8b".into(),
                source: "disk".into(),
                size_bytes: Some(1),
                parameters: None,
                quantization: None,
            },
            InstalledModel {
                name: "qwen2.5-7b-Q4_K_M.gguf".into(),
                source: "disk".into(),
                size_bytes: Some(2),
                parameters: None,
                quantization: None,
            },
        ];
        let merged = merge_models(ollama, disk);
        assert_eq!(merged.len(), 2, "the case-variant duplicate is dropped");
        assert_eq!(merged[0].name, "LLAMA3:8B", "Ollama wins the duplicate");
        assert_eq!(merged[0].source, "ollama", "and it keeps its rich fields");
        assert_eq!(merged[1].name, "qwen2.5-7b-Q4_K_M.gguf");
        assert_eq!(merged[1].source, "disk");
        // Stable UI order: case-insensitive by name.
        assert!(
            merged[0].name.to_lowercase() < merged[1].name.to_lowercase(),
            "the list is sorted by name"
        );
    }

    // -- disk walk ----------------------------------------------------------

    #[test]
    fn walk_respeta_el_limite_de_profundidad() {
        let root = temp_dir("depth");
        plant_file(&root, "a.gguf", b"x"); // depth 0
        plant_file(&root, "L1/b.gguf", b"x"); // depth 1
        plant_file(&root, "L1/L2/c.gguf", b"x"); // depth 2
        plant_file(&root, "L1/L2/L3/d.gguf", b"x"); // depth 3
        plant_file(&root, "L1/L2/L3/L4/e.gguf", b"x"); // depth 4
        plant_file(&root, "L1/L2/L3/L4/L5/f.gguf", b"x"); // depth 5: beyond the cap

        let found = walk_gguf_dirs(&[root]);
        let mut names: Vec<&str> = found.iter().map(|m| m.name.as_str()).collect();
        names.sort();
        assert_eq!(
            names,
            vec![
                "L1/L2/L3/L4/e.gguf",
                "L1/L2/L3/d.gguf",
                "L1/L2/c.gguf",
                "L1/b.gguf",
                "a.gguf",
            ],
            "everything up to 4 levels deep is visited; only depth 5 is not"
        );
        for model in &found {
            assert_eq!(model.source, "disk");
            assert_eq!(model.size_bytes, Some(1));
        }
    }

    #[test]
    fn walk_respeta_el_limite_total_de_archivos() {
        let root = temp_dir("budget");
        for (i, name) in ["one", "two", "three", "four", "five"].iter().enumerate() {
            plant_file(&root, &format!("{name}.gguf"), format!("{i}").as_bytes());
        }

        // Budget smaller than the file count: exactly `budget` files come back
        // (read_dir order decides which ones, never the count).
        let found = walk_gguf_dirs_with_budget(std::slice::from_ref(&root), 3);
        assert_eq!(found.len(), 3, "the walk stops at the budget");
        // Budget larger than the count: everything is found (5 here).
        let found = walk_gguf_dirs_with_budget(&[root], 500);
        assert_eq!(found.len(), 5);
    }

    #[test]
    fn walk_comparte_un_solo_presupuesto_entre_directorios() {
        let root1 = temp_dir("shared-1");
        let root2 = temp_dir("shared-2");
        for (i, name) in ["one", "two", "three"].iter().enumerate() {
            plant_file(&root1, &format!("{name}.gguf"), format!("{i}").as_bytes());
        }
        plant_file(&root2, "four.gguf", b"x");

        // One total budget across directories: 2 + 3 files, budget 4.
        let found = walk_gguf_dirs_with_budget(&[root1, root2], 4);
        assert_eq!(found.len(), 4, "the second directory only gets what is left");
    }

    #[test]
    fn walk_acepta_extension_en_cualquier_mayuscula_y_deriva_los_campos() {
        let root = temp_dir("ext");
        plant_file(&root, "lower.gguf", b"x");
        plant_file(&root, "UPPER.GGUF", b"x");
        plant_file(&root, "Mixed.Gguf", b"x");
        plant_file(&root, "not-a-model.ggu", b"x");
        plant_file(&root, "not-a-model.txt", b"x");
        plant_file(&root, "hinted/qwen2.5-7b-Q4_K_M.gguf", &[0u8; 1024]);

        let found = walk_gguf_dirs(std::slice::from_ref(&root));
        assert_eq!(found.len(), 4, "the extension is matched case-insensitively");

        let hinted = found
            .iter()
            .find(|m| m.name == "hinted/qwen2.5-7b-Q4_K_M.gguf")
            .expect("the hinted model should be found");
        assert_eq!(hinted.size_bytes, Some(1024));
        assert_eq!(hinted.parameters.as_deref(), Some("7b"));
        assert_eq!(hinted.quantization.as_deref(), Some("Q4_K_M"));
        assert_eq!(hinted.name, "hinted/qwen2.5-7b-Q4_K_M.gguf");

        // The bare models carry no name-derived hints at all.
        let bare = found.iter().find(|m| m.name == "lower.gguf").unwrap();
        assert_eq!(bare.parameters, None);
        assert_eq!(bare.quantization, None);
    }

    #[test]
    fn walk_sobrea_directorios_vacios_o_ilegibles_sin_fallar() {
        let root = temp_dir("empty");
        std::fs::create_dir_all(root.join("empty-dir")).expect("test dir creatable");
        assert!(walk_gguf_dirs(&[root]).is_empty());
        // A configured directory that does not exist contributes nothing.
        assert!(walk_gguf_dirs(&[temp_dir("missing-no-mkdir")]).is_empty());
    }

    // -- config ---------------------------------------------------------------

    #[test]
    fn parse_model_dirs_expande_home_y_filtra_lo_inutil() {
        // A fake $HOME whose `models` subdirectory really exists, so the `~/`
        // expansion AND the existence filter can both be exercised.
        let home = temp_dir("home");
        std::fs::create_dir_all(home.join("models")).expect("test dir creatable");
        let existing = temp_dir("exists");
        std::fs::create_dir_all(&existing).expect("test dir creatable");
        let text = format!(
            "model_dirs = [\"~/models\", \"{}\", \"models-relativos\", \"/no/such/dir-xyz\", 42]\n",
            existing.display()
        );
        let dirs = parse_model_dirs_in(&text, Some(home.as_os_str()));
        assert_eq!(
            dirs,
            vec![home.join("models"), existing],
            "~/ expands to $HOME, relative/nonexistent/non-string entries are dropped"
        );
    }

    #[test]
    fn parse_model_dirs_rechaza_home_relative_sin_home() {
        // A `~/models` entry cannot be expanded without $HOME: dropped, never guessed.
        assert!(parse_model_dirs_in("model_dirs = [\"~/models\"]\n", None).is_empty());
    }

    #[test]
    fn parse_model_dirs_sin_clave_o_con_toml_roto_devuelve_vacio() {
        assert!(parse_model_dirs_in("", None).is_empty());
        assert!(parse_model_dirs_in("[ui]\ntheme = \"gentle\"\n", None).is_empty());
        assert!(parse_model_dirs_in("model_dirs = \"nope\"\n", None).is_empty());
        assert!(parse_model_dirs_in("esto no es toml [[", None).is_empty());
        assert!(parse_model_dirs_in("model_dirs = []\n", None).is_empty());
    }
}
