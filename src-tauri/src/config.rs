//! Reading of the shared `config.toml` watch list.
//!
//! The GTK app and the Linux collector (`scripts/pc-ai-stats`) already read
//! `~/.config/pc-ai-monitor/config.toml`: `[[watch]]` blocks choose which
//! process groups the collector builds. This module gives the macOS collector
//! the same file from the same place, so the user configures what to watch
//! once and every platform obeys it. `XDG_CONFIG_HOME` is honoured exactly
//! like the other readers do; nothing here builds a home path from literals.
//!
//! Only the `[[watch]]` table is consumed here: the remaining sections belong
//! to the GTK app. The parsing rules mirror `_load_watch` in
//! `scripts/pc-ai-stats` on purpose — a malformed file or a broken entry must
//! never change what gets monitored, it must fall back to the defaults.

use std::ffi::OsStr;
use std::path::PathBuf;

/// One `[[watch]]` entry: a process group the collector builds.
///
/// `patterns` holds regular expressions searched against the process name
/// (comm) and its full command line, exactly like the Linux collector. An
/// entry without patterns is one of the collector's fixed rules (`system`
/// catches root processes, the other empty-pattern entry catches everything
/// no pattern claimed). `visible` decides whether the group is built at all.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchEntry {
    pub name: String,
    pub patterns: Vec<String>,
    /// Kept for parity with the config the GTK app edits; a UI renders it.
    #[allow(dead_code)]
    pub icon: String,
    pub visible: bool,
}

/// The five groups of always, identical to `DEFAULT_WATCH` in
/// `gui/pc_ai_monitor/config.py` and `WATCH_DEFAULTS` in
/// `scripts/pc-ai-stats`: same names, same anchored patterns, same icons.
pub fn default_watch() -> Vec<WatchEntry> {
    vec![
        WatchEntry {
            name: "pi".into(),
            patterns: vec![r"^pi$".into(), r"/\.pi-lens/".into(), r"/\.pi/agent/".into()],
            icon: "\u{1F967}".into(),
            visible: true,
        },
        WatchEntry {
            name: "hermes".into(),
            patterns: vec![r"^hermes$".into(), r"/\.hermes/".into(), "hermes_cli".into()],
            icon: "\u{1FAB6}".into(),
            visible: true,
        },
        WatchEntry {
            name: "firefox".into(),
            patterns: vec![r"^firefox$".into(), r"/firefox/".into()],
            icon: "\u{1F98A}".into(),
            visible: true,
        },
        WatchEntry {
            name: "system".into(),
            patterns: vec![],
            icon: "\u{2699}\u{FE0F}".into(),
            visible: true,
        },
        WatchEntry {
            name: "other".into(),
            patterns: vec![],
            icon: "\u{1F4E6}".into(),
            visible: true,
        },
    ]
}

/// Resolves the directory that holds `pc-ai-monitor/config.toml` from the
/// same inputs every other reader uses: `XDG_CONFIG_HOME` when set, the home
/// directory's `.config` otherwise. `None` only when neither exists.
pub fn config_dir(
    xdg_config_home: Option<&OsStr>,
    home: Option<&OsStr>,
) -> Option<PathBuf> {
    if let Some(dir) = xdg_config_home.filter(|value| !value.is_empty()) {
        return Some(PathBuf::from(dir).join("pc-ai-monitor"));
    }
    let home = home.filter(|value| !value.is_empty())?;
    Some(PathBuf::from(home).join(".config").join("pc-ai-monitor"))
}

/// The config file the GTK app writes and every collector reads.
pub fn config_path() -> Option<PathBuf> {
    config_dir(
        std::env::var_os("XDG_CONFIG_HOME").as_deref(),
        std::env::var_os("HOME").as_deref(),
    )
    .map(|dir| dir.join("config.toml"))
}

/// `[[watch]]` entries from the shared config file. A missing, unreadable or
/// broken file means the defaults: broken config can never change what is
/// monitored, and it is certainly never fatal.
pub fn load_watch() -> Vec<WatchEntry> {
    let Some(path) = config_path() else {
        return default_watch();
    };
    match std::fs::read_to_string(path) {
        Ok(text) => parse_watch(&text),
        Err(_) => default_watch(),
    }
}

/// Parses `[[watch]]` entries out of a `config.toml` document.
///
/// A section that is absent, not a list, or full of unusable entries means
/// "the usual groups"; a section with at least one valid entry replaces them
/// entirely — the user chose what is watched. An entry needs a name; a
/// `match` that is present but not a list of strings discards the entry.
pub fn parse_watch(text: &str) -> Vec<WatchEntry> {
    match toml::from_str::<toml::Table>(text) {
        Ok(table) => parse_watch_entries(table.get("watch")),
        Err(_) => default_watch(),
    }
}

fn parse_watch_entries(raw: Option<&toml::Value>) -> Vec<WatchEntry> {
    let Some(list) = raw.and_then(toml::Value::as_array) else {
        return default_watch();
    };
    let mut entries = Vec::new();
    for item in list {
        let Some(dict) = item.as_table() else { continue };
        let Some(name) = dict.get("name").and_then(toml::Value::as_str) else {
            continue;
        };
        let name = name.trim().to_lowercase();
        if name.is_empty() {
            continue;
        }
        let patterns = match dict.get("match") {
            None => Vec::new(),
            Some(value) => match value.as_array() {
                Some(list) => list
                    .iter()
                    .filter_map(toml::Value::as_str)
                    .map(str::to_string)
                    .filter(|pattern| !pattern.trim().is_empty())
                    .collect(),
                // A `match` that is not a list of strings is unusable: the
                // whole entry is discarded, exactly like the Linux reader.
                None => continue,
            },
        };
        entries.push(WatchEntry {
            name,
            patterns,
            icon: dict
                .get("icon")
                .and_then(toml::Value::as_str)
                .unwrap_or("")
                .to_string(),
            visible: dict
                .get("visible")
                .and_then(toml::Value::as_bool)
                .unwrap_or(true),
        });
    }
    if entries.is_empty() {
        default_watch()
    } else {
        entries
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_son_los_cinco_grupos_de_siempre() {
        let watch = default_watch();
        let names: Vec<&str> = watch.iter().map(|entry| entry.name.as_str()).collect();
        assert_eq!(names, vec!["pi", "hermes", "firefox", "system", "other"]);
        assert!(watch.iter().all(|entry| entry.visible));
        // Los patrones anclados son los mismos que en el colector de Linux.
        assert_eq!(watch[0].patterns, vec!["^pi$", r"/\.pi-lens/", r"/\.pi/agent/"]);
        assert!(watch[3].patterns.is_empty(), "system es una regla fija, sin patrones");
        assert!(watch[4].patterns.is_empty(), "other es una regla fija, sin patrones");
    }

    #[test]
    fn config_valida_arma_las_entradas_que_puso_el_usuario() {
        let text = r#"
[ui]
theme = "gentle"

[[watch]]
name = "Chrome"
match = ['^chrome$', '/chrome/']
icon = "🌐"
visible = true

[[watch]]
name = "ollama"
match = ["ollama"]

[[watch]]
name = "system"
"#;
        let watch = parse_watch(text);
        assert_eq!(watch.len(), 3, "una entrada valida reemplaza a los defaults");
        assert_eq!(watch[0].name, "chrome", "el nombre se recorta y baja a minusculas");
        assert_eq!(watch[0].patterns, vec!["^chrome$", "/chrome/"]);
        assert_eq!(watch[0].icon, "🌐");
        assert_eq!(watch[1].icon, "", "icono ausente queda vacio");
        assert!(watch[1].visible, "visible ausente es true");
        assert!(watch[2].patterns.is_empty());
    }

    #[test]
    fn config_rota_devuelve_defaults_sin_panic() {
        assert_eq!(parse_watch("esto no es toml [["), default_watch());
        assert_eq!(parse_watch(""), default_watch());
    }

    #[test]
    fn watch_inutil_devuelve_defaults() {
        // Sin seccion watch.
        assert_eq!(parse_watch("[ui]\ntheme = \"gentle\"\n"), default_watch());
        // watch que no es lista.
        assert_eq!(parse_watch("watch = \"nope\"\n"), default_watch());
        // Solo entradas rotas: sin nombre, o match que no es lista.
        let broken = "[[watch]]\nmatch = [\"firefox\"]\n\n\
                      [[watch]]\nname = \"firefox\"\nmatch = \"no-es-lista\"\n";
        assert_eq!(parse_watch(broken), default_watch());
    }

    #[test]
    fn entrada_visible_false_se_conserva_para_el_colector() {
        let text = "[[watch]]\nname = \"chrome\"\nmatch = ['chrome']\nvisible = false\n";
        let watch = parse_watch(text);
        assert_eq!(watch.len(), 1);
        assert!(!watch[0].visible);
    }

    #[test]
    fn xdg_config_home_manda_sobre_home() {
        let dir = config_dir(Some("/tmp/xdg-config".as_ref()), Some("/usuarios/alguien".as_ref()))
            .expect("con XDG definido hay directorio");
        assert_eq!(dir, PathBuf::from("/tmp/xdg-config/pc-ai-monitor"));
    }

    #[test]
    fn sin_xdg_se_cae_a_home_dot_config() {
        let dir = config_dir(None, Some("/usuarios/alguien".as_ref()))
            .expect("con HOME definido hay directorio");
        assert_eq!(dir, PathBuf::from("/usuarios/alguien/.config/pc-ai-monitor"));
    }

    #[test]
    fn sin_ninguna_base_no_hay_ruta_inventada() {
        assert_eq!(config_dir(None, None), None);
        assert_eq!(config_dir(Some("".as_ref()), Some("".as_ref())), None);
    }
}
