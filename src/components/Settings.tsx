import { useCallback, useEffect, useRef, useState } from "react";
import type { WatchConfig, WatchEntry } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * True when the frontend runs inside a Tauri webview.
 *
 * Same detection as `useAppState` (`__TAURI_INTERNALS__` is injected by Tauri
 * v2 before any page script runs), repeated locally because the settings
 * screen talks to a different backend: the daemon's HTTP config API, which
 * only exists when the app is served by the daemon in a plain browser.
 * Inside the Tauri shell there is no `/api/config` endpoint at all, so the
 * screen explains itself instead of showing a broken UI.
 */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * The five groups of always, identical to `default_watch()` in
 * src-tauri/src/config.rs (and to `WATCH_DEFAULTS` in the Linux collector):
 * same names, same anchored patterns, same icons. Used only as the starting
 * point the empty state offers; the server is the single source of truth for
 * anything that gets saved.
 */
function defaultWatch(): WatchEntry[] {
  return [
    {
      name: "pi",
      match: ["^pi$", "/\\.pi-lens/", "/\\.pi/agent/"],
      icon: "\u{1F967}",
      visible: true,
    },
    {
      name: "hermes",
      match: ["^hermes$", "/\\.hermes/", "hermes_cli"],
      icon: "\u{1FAB6}",
      visible: true,
    },
    {
      name: "firefox",
      match: ["^firefox$", "/firefox/"],
      icon: "\u{1F98A}",
      visible: true,
    },
    { name: "system", match: [], icon: "\u{2699}\u{FE0F}", visible: true },
    { name: "other", match: [], icon: "\u{1F4E6}", visible: true },
  ];
}

/**
 * One editable row. The patterns live in the draft as the raw text the user
 * types (comma-separated) so every keystroke is preserved; the array form is
 * rebuilt only when saving. `key` is a stable React key that survives name
 * edits (the server's `name` is not a usable identity while being edited).
 */
interface DraftEntry {
  key: number;
  name: string;
  icon: string;
  visible: boolean;
  matchText: string;
}

let nextDraftKey = 1;

function toDraft(entry: WatchEntry): DraftEntry {
  return {
    key: nextDraftKey++,
    name: entry.name,
    icon: entry.icon,
    visible: entry.visible,
    matchText: entry.match.join(", "),
  };
}

function fromDraft(draft: DraftEntry): WatchEntry {
  return {
    name: draft.name.trim(),
    match: draft.matchText
      .split(",")
      .map((pattern) => pattern.trim())
      // Blank patterns would match every process; the daemon drops them
      // silently too, but the UI sends what the user actually meant.
      .filter((pattern) => pattern.length > 0),
    icon: draft.icon.trim(),
    visible: draft.visible,
  };
}

type LoadStatus = "loading" | "ready" | "unavailable";

type SaveStatus = "idle" | "saving" | "saved";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Settings() {
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<DraftEntry[]>([]);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const inFlight = useRef(false);

  // ── Load the current config from the daemon ─────────────────────────────

  const loadConfig = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await fetch("/api/config");
      if (!res.ok) {
        throw new Error(`GET /api/config failed with status ${res.status}`);
      }
      const config = (await res.json()) as WatchConfig;
      setDrafts(Array.isArray(config.watch) ? config.watch.map(toDraft) : []);
      setLoadStatus("ready");
      setLoadError(null);
    } catch (err) {
      // In the Tauri shell this screen has no backend at all; that is a
      // different, quieter situation than a daemon that answered badly.
      setLoadError(err instanceof Error ? err.message : String(err));
      setLoadStatus("unavailable");
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    if (isTauri()) {
      setLoadStatus("unavailable");
      return;
    }
    void loadConfig();
  }, [loadConfig]);

  // ── Draft mutations ─────────────────────────────────────────────────────

  const updateDraft = (key: number, patch: Partial<DraftEntry>) => {
    setDrafts((prev) =>
      prev.map((draft) => (draft.key === key ? { ...draft, ...patch } : draft)),
    );
  };

  const removeDraft = (key: number) => {
    setDrafts((prev) => prev.filter((draft) => draft.key !== key));
  };

  const addDraft = () => {
    setDrafts((prev) => [
      ...prev,
      toDraft({ name: "", match: [], icon: "\u{1F4E6}", visible: true }),
    ]);
  };

  const startFromDefaults = () => {
    setDrafts(defaultWatch().map(toDraft));
  };

  // ── Save: POST the whole list, keep every keystroke on failure ──────────

  const save = async () => {
    if (saveStatus === "saving") return;
    setSaveStatus("saving");
    setSaveError(null);
    const body: WatchConfig = { watch: drafts.map(fromDraft) };
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.status === 400) {
        // The server's validation message (plain text). The drafts are left
        // exactly as they were: the user fixes the problem, nothing is lost.
        const message = (await res.text()).trim();
        setSaveError(message || `El servidor rechazó la config (400).`);
        setSaveStatus("idle");
        return;
      }
      if (!res.ok) {
        setSaveError(
          `El daemon respondió con el estado ${res.status}; no se guardó nada.`,
        );
        setSaveStatus("idle");
        return;
      }
      // 200: adopt the config as re-read from disk, so the list shown is
      // exactly what the next sampling cycle will use.
      const config = (await res.json()) as WatchConfig;
      setDrafts(Array.isArray(config.watch) ? config.watch.map(toDraft) : []);
      setSaveStatus("saved");
    } catch {
      setSaveError(
        "No se pudo contactar al daemon: esta pantalla necesita que la app " +
          "esté servida por el daemon (en el navegador), no funciona dentro " +
          "de la app de escritorio.",
      );
      setSaveStatus("idle");
    }
  };

  // ── Unavailable: Tauri shell, or the daemon never answered ──────────────

  if (loadStatus === "unavailable") {
    return (
      <div className="settings-content">
        {isTauri() ? (
          <div className="settings-notice">
            <p>
              Esta pantalla necesita el <strong>daemon</strong>: abre la app en
              el navegador (servida por el daemon) para elegir qué se
              monitorea. La app de escritorio no expone la API de config.
            </p>
          </div>
        ) : (
          <div className="error-banner">
            ⚠️ No se pudo leer la config del daemon ({loadError}).{" "}
            <button
              type="button"
              className="settings-button"
              onClick={() => void loadConfig()}
            >
              Reintentar
            </button>
          </div>
        )}
      </div>
    );
  }

  // ── Loading ─────────────────────────────────────────────────────────────

  if (loadStatus === "loading") {
    return (
      <div className="loading-state">
        <span className="spinner" />
        <span>Leyendo la config del daemon…</span>
      </div>
    );
  }

  // ── Ready ───────────────────────────────────────────────────────────────

  const empty = drafts.length === 0;

  return (
    <div className="settings-content">
      <div className="section">
        <div className="section-title">Qué se monitorea</div>
        <div className="section-body">
          <p className="settings-explain">
            Cada entrada agrupa los procesos cuyo nombre o línea de comando
            coincide con alguno de sus patrones (expresiones regulares) y decide
            si ese grupo aparece en las mediciones. Una entrada{" "}
            <em>sin</em> patrones es una regla fija del colector:{" "}
            <code>system</code> junta los procesos root y <code>other</code>{" "}
            junta todo lo que ningún patrón reclamó. Desmarcar{" "}
            <code>visible</code> hace que el grupo no se mida en absoluto.
          </p>
          <p className="settings-explain">
            Los cambios se aplican solos: el colector relee esta config en cada
            ciclo de medición, así que <strong>no hace falta reiniciar
            nada</strong>. Se escribe en{" "}
            <code>~/.config/pc-ai-monitor/config.toml</code>, en la máquina
            donde corre el daemon, y el resto del archivo queda intacto.
          </p>

          {loadError && (
            <div className="error-banner">⚠️ {loadError}</div>
          )}

          {saveError && (
            <div className="error-banner">⚠️ {saveError}</div>
          )}

          {saveStatus === "saved" && (
            <div className="settings-saved">
              ✔ Guardado: la lista de arriba es la config que devolvió el
              servidor, ya activa para la próxima medición.
            </div>
          )}

          {empty ? (
            <div className="placeholder">
              <p>
                No hay entradas de monitoreo: el colector usará sus grupos por
                defecto hasta que guardes una lista.
              </p>
              <button
                type="button"
                className="settings-button"
                onClick={startFromDefaults}
              >
                Partir de las entradas por defecto
              </button>
            </div>
          ) : (
            <div className="settings-list">
              {drafts.map((draft) => (
                <div key={draft.key} className="settings-entry">
                  <label className="settings-switch">
                    <input
                      type="checkbox"
                      checked={draft.visible}
                      onChange={(e) =>
                        updateDraft(draft.key, { visible: e.target.checked })
                      }
                    />
                    <span>visible</span>
                  </label>

                  <input
                    className="settings-input settings-icon"
                    value={draft.icon}
                    aria-label="Ícono"
                    placeholder="Ícono"
                    onChange={(e) =>
                      updateDraft(draft.key, { icon: e.target.value })
                    }
                  />

                  <input
                    className="settings-input settings-name"
                    value={draft.name}
                    aria-label="Nombre del grupo"
                    placeholder="Nombre del grupo"
                    onChange={(e) =>
                      updateDraft(draft.key, { name: e.target.value })
                    }
                  />

                  <input
                    className="settings-input settings-match"
                    value={draft.matchText}
                    aria-label="Patrones de coincidencia"
                    placeholder="Patrones de coincidencia, separados por comas (regex)"
                    onChange={(e) =>
                      updateDraft(draft.key, { matchText: e.target.value })
                    }
                  />

                  <button
                    type="button"
                    className="settings-button settings-remove"
                    aria-label={`Borrar ${draft.name || "entrada"}`}
                    onClick={() => removeDraft(draft.key)}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="settings-actions">
            <button
              type="button"
              className="settings-button"
              onClick={addDraft}
            >
              + Agregar entrada
            </button>
            <button
              type="button"
              className="settings-button settings-save"
              disabled={saveStatus === "saving" || empty}
              onClick={() => void save()}
            >
              {saveStatus === "saving" ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
