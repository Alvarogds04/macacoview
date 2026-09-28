//! Reading of the shared `config.toml` watch list.
//!
//! The GTK app and the Linux collector (`scripts/pc-ai-stats`) already read
//! `~/.config/macacoview/config.toml`: `[[watch]]` blocks choose which
//! process groups the collector builds. This module gives the macOS collector
//! the same file from the same place, so the user configures what to watch
//! once and every platform obeys it. `XDG_CONFIG_HOME` is honoured exactly
//! like the other readers do; nothing here builds a home path from literals.
//!
//! The config used to live in `~/.config/pc-ai-monitor/`; the first reader
//! that needs it after the rename copies it over to the new directory (a
//! copy, never a move: the original stays untouched until the user removes
//! it). Only `config.toml` migrates — other files that live next to it are
//! not ours to touch.
//!
//! Only the `[[watch]]` table is consumed here: the remaining sections belong
//! to the GTK app. The parsing rules mirror `_load_watch` in
//! `scripts/pc-ai-stats` on purpose — a malformed file or a broken entry must
//! never change what gets monitored, it must fall back to the defaults.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use toml_edit::RawString;

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

/// Resolves the directory that holds `<app_dir>/config.toml` from the
/// same inputs every other reader uses: `XDG_CONFIG_HOME` when set, the home
/// directory's `.config` otherwise. `None` only when neither exists.
fn config_dir_named(
    xdg_config_home: Option<&OsStr>,
    home: Option<&OsStr>,
    app_dir: &str,
) -> Option<PathBuf> {
    if let Some(dir) = xdg_config_home.filter(|value| !value.is_empty()) {
        return Some(PathBuf::from(dir).join(app_dir));
    }
    let home = home.filter(|value| !value.is_empty())?;
    Some(PathBuf::from(home).join(".config").join(app_dir))
}

/// Resolves the directory that holds `macacoview/config.toml` from the same
/// inputs every other reader uses: `XDG_CONFIG_HOME` when set, the home
/// directory's `.config` otherwise. `None` only when neither exists.
pub fn config_dir(
    xdg_config_home: Option<&OsStr>,
    home: Option<&OsStr>,
) -> Option<PathBuf> {
    config_dir_named(xdg_config_home, home, "macacoview")
}

/// The directory the config lived in before the rename to `macacoview`.
/// Only the migration reads from here; nothing ever writes to it.
pub fn legacy_config_dir(
    xdg_config_home: Option<&OsStr>,
    home: Option<&OsStr>,
) -> Option<PathBuf> {
    config_dir_named(xdg_config_home, home, "pc-ai-monitor")
}

/// Copies the legacy `pc-ai-monitor/config.toml` into the new `macacoview`
/// directory when the config is needed and the new file is not there yet.
///
/// The rules, in order: a config that already exists at the new path is
/// never overwritten; with no legacy file there is nothing to do and no
/// directory is invented. When the copy does happen it is a copy, never a
/// move or a delete — the original stays where it was so the user can remove
/// it once they trust the migration. Only `config.toml` is copied: other
/// files that live next to it (backups and such) are not ours to touch.
/// Any failure is reported as `false` and never breaks the caller.
pub fn migrate_legacy_config(new_dir: &Path, legacy_dir: &Path) -> bool {
    let new_path = new_dir.join("config.toml");
    let legacy_path = legacy_dir.join("config.toml");
    if new_path.exists() || !legacy_path.is_file() {
        return false;
    }
    if std::fs::create_dir_all(new_dir).is_err() {
        return false;
    }
    std::fs::copy(&legacy_path, &new_path).is_ok()
}

/// The config file the GTK app writes and every collector reads. The first
/// resolution after the rename brings the legacy config over (a copy), so
/// the user keeps their watch entries, bar settings and models.
pub fn config_path() -> Option<PathBuf> {
    let xdg_var = std::env::var_os("XDG_CONFIG_HOME");
    let home_var = std::env::var_os("HOME");
    let xdg = xdg_var.as_deref();
    let home = home_var.as_deref();
    let dir = config_dir(xdg, home)?;
    if let Some(legacy) = legacy_config_dir(xdg, home) {
        migrate_legacy_config(&dir, &legacy);
    }
    Some(dir.join("config.toml"))
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

// ---------------------------------------------------------------------------
// Writing the watch list (daemon POST /api/config)
// ---------------------------------------------------------------------------

/// Replaces the `[[watch]]` blocks of the config file, leaving everything
/// else — other sections, their values, their comments — byte-for-byte
/// intact. Implemented with `toml_edit` for exactly that guarantee; a plain
/// `toml` round-trip would silently drop the user's comments.
///
/// A file that is not valid TOML is refused instead of overwritten: this
/// module can never be the thing that destroys a config it cannot parse.
/// The write is atomic (sibling temp file + rename, both inside the config
/// directory) so a collector sampling mid-write never sees a half file.
/// Writes stay strictly inside the config path: nothing here resolves
/// anywhere else.
pub fn write_watch(path: &Path, entries: &[WatchEntry]) -> std::io::Result<()> {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let mut doc = match text.parse::<toml_edit::DocumentMut>() {
        Ok(doc) => doc,
        Err(err) => {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("{} is not valid TOML, refusing to edit it: {err}", path.display()),
            ));
        }
    };

    // Drop the old watch blocks and append the new ones at the end; every
    // other table keeps its position, format and comments untouched.
    //
    // Comments written directly above the old `[[watch]]` headers are decor
    // of those blocks in `toml_edit`; they are carried over to the new first
    // block so replacing watch does not silently eat the user's notes.
    let old_prefix = match doc.get("watch") {
        Some(toml_edit::Item::ArrayOfTables(old)) => old
            .iter()
            .next()
            .and_then(|table| table.decor().prefix().and_then(RawString::as_str).map(str::to_string)),
        _ => None,
    };
    doc.remove("watch");
    if !entries.is_empty() {
        let mut watch = toml_edit::ArrayOfTables::new();
        for entry in entries {
            let mut table = toml_edit::Table::new();
            table["name"] = toml_edit::value(entry.name.trim().to_string());
            let mut patterns = toml_edit::Array::new();
            for pattern in &entry.patterns {
                patterns.push(pattern.as_str());
            }
            table["match"] = toml_edit::value(patterns);
            table["icon"] = toml_edit::value(entry.icon.as_str());
            table["visible"] = toml_edit::value(entry.visible);
            watch.push(table);
        }
        if let Some(prefix) = old_prefix {
            if let Some(first) = watch.iter_mut().next() {
                first.decor_mut().set_prefix(prefix);
            }
        }
        doc.insert("watch", toml_edit::Item::ArrayOfTables(watch));
    }

    // Atomic swap: write the sibling temp file first, then rename over the
    // real config. Both live in the config directory, never anywhere else.
    let serialized = doc.to_string();
    let dir = path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("{} has no parent directory", path.display()),
        )
    })?;
    std::fs::create_dir_all(dir)?;
    let tmp = dir.join(".config.toml.watch.tmp");
    let write_result = std::fs::write(&tmp, &serialized);
    match write_result {
        Ok(()) => {}
        Err(err) => {
            let _ = std::fs::remove_file(&tmp); // no temp file left behind
            return Err(err);
        }
    }
    if let Err(err) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }
    Ok(())
}

/// Writes the watch list to the user's shared config (the one every reader
/// resolves from `XDG_CONFIG_HOME`). An error means nothing was written.
pub fn save_watch(entries: &[WatchEntry]) -> std::io::Result<()> {
    let Some(path) = config_path() else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "no config directory could be resolved from XDG_CONFIG_HOME or HOME",
        ));
    };
    write_watch(&path, entries)
}

/// Validates a `POST /api/config` body and turns it into watch entries.
///
/// The shape is the same the daemon serves: `{"watch": [{"name", "match",
/// "icon", "visible"}]}`. Only `name` is mandatory (`match` defaults to no
/// patterns, `icon` to empty, `visible` to true); anything present must have
/// the right type. Any validation failure is a hard error — the daemon turns
/// it into a 400 and the file is never touched.
pub fn watch_from_json(body: &serde_json::Value) -> Result<Vec<WatchEntry>, String> {
    let Some(list) = body
        .as_object()
        .ok_or("the body must be a JSON object with a \"watch\" array")?
        .get("watch")
        .and_then(serde_json::Value::as_array)
    else {
        return Err("the body must be a JSON object with a \"watch\" array".into());
    };
    let mut entries = Vec::new();
    for item in list {
        let Some(dict) = item.as_object() else {
            return Err("every entry of \"watch\" must be a JSON object".into());
        };
        let name = match dict.get("name") {
            Some(serde_json::Value::String(name)) => name.trim().to_string(),
            _ => return Err("every watch entry needs a non-empty \"name\" string".into()),
        };
        if name.is_empty() {
            return Err("every watch entry needs a non-empty \"name\" string".into());
        }
        let patterns = match dict.get("match") {
            None => Vec::new(),
            Some(serde_json::Value::Array(items)) => {
                let mut patterns = Vec::with_capacity(items.len());
                for item in items {
                    match item.as_str() {
                        // Blank patterns would match every process; the
                        // reader (`parse_watch`) filters them silently, so
                        // the writer mirrors that instead of erroring.
                        Some(pattern) if !pattern.trim().is_empty() => {
                            patterns.push(pattern.to_string())
                        }
                        Some(_) => {}
                        None => return Err("\"match\" must be a list of strings".into()),
                    }
                }
                patterns
            }
            Some(_) => return Err("\"match\" must be a list of strings".into()),
        };
        let icon = match dict.get("icon") {
            None => String::new(),
            Some(serde_json::Value::String(icon)) => icon.clone(),
            Some(_) => return Err("\"icon\" must be a string".into()),
        };
        let visible = match dict.get("visible") {
            None => true,
            Some(serde_json::Value::Bool(visible)) => *visible,
            Some(_) => return Err("\"visible\" must be a boolean".into()),
        };
        entries.push(WatchEntry { name, patterns, icon, visible });
    }
    Ok(entries)
}

/// Serializes watch entries into the JSON shape the daemon serves for
/// `GET /api/config` (note `match`, the config-file spelling, not `patterns`).
pub fn watch_json(entries: &[WatchEntry]) -> serde_json::Value {
    serde_json::Value::Array(
        entries
            .iter()
            .map(|entry| {
                serde_json::json!({
                    "name": entry.name,
                    "match": entry.patterns,
                    "icon": entry.icon,
                    "visible": entry.visible,
                })
            })
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

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
        assert_eq!(dir, PathBuf::from("/tmp/xdg-config/macacoview"));
    }

    #[test]
    fn sin_xdg_se_cae_a_home_dot_config() {
        let dir = config_dir(None, Some("/usuarios/alguien".as_ref()))
            .expect("con HOME definido hay directorio");
        assert_eq!(dir, PathBuf::from("/usuarios/alguien/.config/macacoview"));
    }

    #[test]
    fn el_directorio_legacy_sigue_resolviendo_a_pc_ai_monitor() {
        assert_eq!(
            legacy_config_dir(None, Some("/usuarios/alguien".as_ref())),
            Some(PathBuf::from("/usuarios/alguien/.config/pc-ai-monitor"))
        );
        assert_eq!(
            legacy_config_dir(Some("/tmp/xdg-config".as_ref()), None),
            Some(PathBuf::from("/tmp/xdg-config/pc-ai-monitor"))
        );
        assert_eq!(legacy_config_dir(None, None), None);
    }

    #[test]
    fn sin_ninguna_base_no_hay_ruta_inventada() {
        assert_eq!(config_dir(None, None), None);
        assert_eq!(config_dir(Some("".as_ref()), Some("".as_ref())), None);
    }

    // -------------------------------------------------------------------
    // Migración del config viejo (pc-ai-monitor -> macacoview)
    // -------------------------------------------------------------------

    #[test]
    fn migracion_copia_el_config_viejo_cuando_el_nuevo_no_existe() {
        let dir = temp_config_dir("migra");
        let new_dir = dir.join("macacoview");
        let legacy_dir = dir.join("pc-ai-monitor");
        let legacy = write_config_file(&dir, "[[watch]]\nname = \"chrome\"\n");
        // write_config_file crea `dir/pc-ai-monitor`, que es justo el legacy.
        assert_eq!(legacy.parent(), Some(legacy_dir.as_path()));

        assert!(migrate_legacy_config(&new_dir, &legacy_dir));

        let migrated = new_dir.join("config.toml");
        assert_eq!(
            std::fs::read_to_string(&migrated).expect("la copia deberia existir"),
            "[[watch]]\nname = \"chrome\"\n",
            "el contenido migrado queda igual al original"
        );
        assert!(legacy.is_file(), "el original nunca se borra ni se mueve");
        assert_eq!(
            std::fs::read_to_string(&legacy).expect("el original deberia seguir ahi"),
            "[[watch]]\nname = \"chrome\"\n",
            "el original queda intacto"
        );
    }

    #[test]
    fn migracion_no_pisa_un_config_nuevo_ya_existente() {
        let dir = temp_config_dir("no-pisa");
        let new_dir = dir.join("macacoview");
        let legacy_dir = dir.join("pc-ai-monitor");
        let _legacy = write_config_file(&dir, "[[watch]]\nname = \"viejo\"\n");
        std::fs::create_dir_all(&new_dir).expect("deberia crear el directorio nuevo");
        let new_path = new_dir.join("config.toml");
        std::fs::write(&new_path, "[[watch]]\nname = \"nuevo\"\n")
            .expect("deberia escribir la config nueva de prueba");

        assert!(!migrate_legacy_config(&new_dir, &legacy_dir));
        assert_eq!(
            std::fs::read_to_string(&new_path).expect("la config nueva deberia seguir ahi"),
            "[[watch]]\nname = \"nuevo\"\n",
            "el config nuevo nunca se pisa con el viejo"
        );
    }

    #[test]
    fn sin_config_viejo_la_migracion_no_inventa_nada() {
        let dir = temp_config_dir("sin-viejo");
        let new_dir = dir.join("macacoview");
        let legacy_dir = dir.join("pc-ai-monitor"); // existe el dir, no el archivo
        std::fs::create_dir_all(&legacy_dir).expect("deberia crear el directorio de prueba");

        assert!(!migrate_legacy_config(&new_dir, &legacy_dir));
        assert!(!new_dir.exists(), "sin nada que migrar no se crea el directorio nuevo");

        // Ni siquiera existiendo el directorio legacy.
        let dir = temp_config_dir("sin-dir-viejo");
        let new_dir = dir.join("macacoview");
        assert!(!migrate_legacy_config(&new_dir, &dir.join("pc-ai-monitor")));
        assert!(!new_dir.exists());
    }

    #[test]
    fn la_migracion_copia_solo_el_config_toml() {
        let dir = temp_config_dir("solo-toml");
        let new_dir = dir.join("macacoview");
        let legacy_dir = dir.join("pc-ai-monitor");
        let _legacy = write_config_file(&dir, "[ui]\ntheme = \"gentle\"\n");
        std::fs::write(legacy_dir.join(".bak-precfg-bar"), "no es nuestro")
            .expect("deberia escribir el archivo ajeno de prueba");

        assert!(migrate_legacy_config(&new_dir, &legacy_dir));
        assert_eq!(std::fs::read_dir(&new_dir).expect("deberia leer el dir nuevo").count(), 1,
            "solo config.toml viaja al directorio nuevo");
    }

    // -------------------------------------------------------------------
    // Escritura de [[watch]] (API de configuración del daemon)
    // -------------------------------------------------------------------

    /// Un directorio temporal único por prueba: la config de pruebas nunca
    /// toca la del usuario. El directorio se reutiliza (no se borra) porque
    /// las pruebas no tienen permiso para hacer limpieza destructiva.
    fn temp_config_dir(tag: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("el reloj del sistema deberia avanzar")
            .as_nanos();
        std::env::temp_dir().join(format!("pc-ai-monitor-cfg-{tag}-{unique}"))
    }

    fn write_config_file(dir: &Path, text: &str) -> PathBuf {
        let dir = dir.join("pc-ai-monitor");
        std::fs::create_dir_all(&dir).expect("deberia poder crear el directorio temporal");
        let path = dir.join("config.toml");
        std::fs::write(&path, text).expect("deberia poder escribir la config de prueba");
        path
    }

    #[test]
    fn escribir_watch_conserva_las_otras_secciones_y_los_comentarios() {
        let dir = temp_config_dir("conserva");
        let original = "# Configuracion del usuario\n\n[bar]\nheight = 34\n\n[refresh]\ninterval = 2\n# un comentario que debe sobrevivir\n\n[[watch]]\nname = \"firefox\"\nmatch = ['firefox']\n";
        let path = write_config_file(&dir, original);

        let entries = vec![WatchEntry {
            name: "chrome".into(),
            patterns: vec!["^chrome$".into()],
            icon: "🌐".into(),
            visible: true,
        }];
        write_watch(&path, &entries).expect("la escritura deberia funcionar");

        let written = std::fs::read_to_string(&path).expect("la config deberia existir");
        assert!(written.contains("[bar]"), "la seccion [bar] debe sobrevivir");
        assert!(written.contains("[refresh]"), "la seccion [refresh] debe sobrevivir");
        assert!(written.contains("height = 34"), "el contenido de [bar] debe sobrevivir");
        assert!(written.contains("interval = 2"), "el contenido de [refresh] debe sobrevivir");
        assert!(
            written.contains("# un comentario que debe sobrevivir"),
            "los comentarios ajenos a watch deben sobrevivir"
        );
        assert!(written.contains("chrome"), "el watch nuevo debe estar");
        assert!(!written.contains("firefox"), "el watch viejo debe ser reemplazado");
        // Y el archivo sigue siendo una config valida para el lector.
        let watch = parse_watch(&written);
        assert_eq!(watch.len(), 1);
        assert_eq!(watch[0].name, "chrome");
    }

    #[test]
    fn escribir_watch_reemplaza_solo_los_bloques_watch() {
        let dir = temp_config_dir("reemplaza");
        let original = "[ui]\ntheme = \"gentle\"\n\n[[watch]]\nname = \"viejo\"\nmatch = ['viejo']\nicon = \"old\"\n\n[[watch]]\nname = \"otro\"\n";
        let path = write_config_file(&dir, original);

        let entries = vec![
            WatchEntry { name: "uno".into(), patterns: vec!["^uno$".into()], icon: "1".into(), visible: true },
            WatchEntry { name: "dos".into(), patterns: vec![], icon: "".into(), visible: false },
        ];
        write_watch(&path, &entries).expect("la escritura deberia funcionar");

        let written = std::fs::read_to_string(&path).expect("la config deberia existir");
        assert!(written.contains("[ui]"), "la seccion [ui] debe sobrevivir");
        let watch = parse_watch(&written);
        assert_eq!(watch.len(), 2, "los bloques viejos se reemplazan");
        assert_eq!(watch[0].name, "uno");
        assert_eq!(watch[0].patterns, vec!["^uno$"]);
        assert_eq!(watch[0].icon, "1");
        assert!(watch[1].patterns.is_empty());
        assert!(!watch[1].visible);
    }

    #[test]
    fn escribir_watch_crea_el_archivo_si_no_existe() {
        let dir = temp_config_dir("crea");
        let path = dir.join("pc-ai-monitor").join("config.toml");
        let entries = vec![WatchEntry {
            name: "ollama".into(),
            patterns: vec!["ollama".into()],
            icon: "".into(),
            visible: true,
        }];
        write_watch(&path, &entries).expect("deberia crear directorio y archivo");
        let written = std::fs::read_to_string(&path).expect("la config deberia existir");
        assert_eq!(parse_watch(&written), entries);
    }

    #[test]
    fn escribir_watch_rehusa_un_archivo_toml_roto_sin_destruirlo() {
        let dir = temp_config_dir("roto");
        let original = "esto no es toml [[";
        let path = write_config_file(&dir, original);
        let entries = vec![WatchEntry {
            name: "chrome".into(),
            patterns: vec![],
            icon: "".into(),
            visible: true,
        }];
        let result = write_watch(&path, &entries);
        assert!(result.is_err(), "un archivo roto no se puede editar con seguridad");
        assert_eq!(
            std::fs::read_to_string(&path).expect("el archivo deberia seguir ahi"),
            original,
            "el contenido roto debe quedar intacto"
        );
    }

    #[test]
    fn watch_from_json_valida_el_cuerpo_del_post() {
        // Cuerpo bien formado.
        let body: serde_json::Value = serde_json::from_str(
            r#"{"watch": [{"name": "Chrome", "match": ["^chrome$", ""], "icon": "🌐", "visible": false}]}"#,
        )
        .expect("el cuerpo de prueba deberia ser JSON valido");
        let entries = watch_from_json(&body).expect("un cuerpo valido deberia aceptarse");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "Chrome");
        assert_eq!(entries[0].patterns, vec!["^chrome$"], "los patrones vacios se descartan");
        assert_eq!(entries[0].icon, "🌐");
        assert!(!entries[0].visible);

        // Campos opcionales con defaults.
        let minimal: serde_json::Value =
            serde_json::from_str(r#"{"watch": [{"name": "pi"}]}"#).unwrap();
        let entries = watch_from_json(&minimal).expect("solo el nombre es obligatorio");
        assert_eq!(entries[0].patterns, Vec::<String>::new());
        assert_eq!(entries[0].icon, "");
        assert!(entries[0].visible);

        // Malformados: todos 400 en el daemon.
        let bad_cases: Vec<(&str, serde_json::Value)> = vec![
            ("no es un objeto", serde_json::json!("nope")),
            ("sin watch", serde_json::json!({"other": []})),
            ("watch no es lista", serde_json::json!({"watch": "nope"})),
            ("entrada no es objeto", serde_json::json!({"watch": ["nope"]})),
            ("sin nombre", serde_json::json!({"watch": [{"match": ["x"]}]})),
            ("nombre no es string", serde_json::json!({"watch": [{"name": 3}]})),
            ("nombre vacio", serde_json::json!({"watch": [{"name": "   "}]})),
            ("match no es lista", serde_json::json!({"watch": [{"name": "x", "match": "nope"}]})),
            ("match con no-strings", serde_json::json!({"watch": [{"name": "x", "match": ["ok", 3]}]})),
            ("icono no es string", serde_json::json!({"watch": [{"name": "x", "icon": 3}]})),
            ("visible no es bool", serde_json::json!({"watch": [{"name": "x", "visible": "si"}]})),
        ];
        for (why, value) in bad_cases {
            assert!(watch_from_json(&value).is_err(), "{why} deberia rechazarse");
        }
    }

    #[test]
    fn watch_json_tiene_la_forma_del_contrato() {
        let entries = vec![WatchEntry {
            name: "pi".into(),
            patterns: vec!["^pi$".into()],
            icon: "🍎".into(),
            visible: true,
        }];
        let json = watch_json(&entries);
        let list = json.as_array().expect("watch debe ser una lista");
        assert_eq!(list.len(), 1);
        let dict = list[0].as_object().expect("cada entrada debe ser un objeto");
        // Exactamente las claves del contrato (el orden no esta fijado: el
        // mapa JSON de serde ordena alfabeticamente por defecto).
        assert_eq!(dict.len(), 4);
        assert!(dict.contains_key("name"));
        assert!(dict.contains_key("match"));
        assert!(dict.contains_key("icon"));
        assert!(dict.contains_key("visible"));
        assert_eq!(
            json,
            serde_json::json!([{ "name": "pi", "match": ["^pi$"], "icon": "🍎", "visible": true }])
        );
    }
}
