# Cierre: rc2, inventario de modelos y la Mac ajena

Origen: pedido del usuario del 28/09 (tres items, en este orden).

Estado medido al abrir, no recordado:

- El repo ya se llama `Alvarogds04/macacoview` y es **publico**. El renombre
  (collectors, launcher, uuid, daemon, config dir) se mergeo el 28/09, **despues**
  de la unica release publicada.
- Unica release: `v0.2.0-rc1` (2026-09-27, pre-release), con assets del nombre
  viejo: `pc-ai-monitor-serve-aarch64-apple-darwin.tar.gz` y
  `pc-ai-monitor-v0.2.0-rc1-linux.tar.gz`.
- Version declarada en los cuatro lugares habituales y **los cuatro decian
  0.1.0**: `package.json`, `src-tauri/Cargo.toml`, `src-tauri/Cargo.lock`,
  `src-tauri/tauri.conf.json`. Habia dos mas sin declarar en ningun lado:
  `package-lock.json` y `gui/pc_ai_monitor/__init__.py`.
- Run de la rc1 (id 36335457200): `package` success, `package-macos
  (aarch64-apple-darwin, macos-14)` success, `package-macos
  (x86_64-apple-darwin, macos-13)` **cancelled tras 24 h en cola**. GitHub retiro
  `macos-13` en diciembre de 2025; el unico runner Intel que queda es
  `macos-15-intel` (disponible hasta agosto de 2027).
- `gui/install.sh --from-release` sin tag resolvia con `api/releases/latest`.
  Medido contra la API: ese endpoint responde **HTTP 404** mientras la ultima
  release sea un rc; `releases?per_page=10` responde 200. El camino
  "recomendado" del README no encontraba nada.
- `scripts/mac-verify.sh` buscaba el binario `pc-ai-monitor` en cinco rutas
  viejas (incluida `/Applications/pc-ai-monitor.app`, que era el bundle Tauri
  jubilado) y lo nombraba en su aviso final; `odd/tasks/macos-runbook.md`
  arrancaba el daemon como `pc-ai-monitor-serve`. Los dos quedaron atras del
  renombre.
- `src/types.ts` documentaba `~/.config/pc-ai-monitor/config.toml`, pero el
  config se mudo a `~/.config/macacoview/` (commit 67765bf, con migracion).
  `package-lock.json` declaraba el nombre viejo `pc-ai-monitor` mientras
  `package.json` ya decia `@alvarogds04/pc-ai-monitor`.

## Linea 1: v0.2.0-rc2 (release y README dicen lo mismo)

Qué tiene que ser verdad al cerrar: el tag `v0.2.0-rc2` publica assets con el
nombre nuevo (`macacoview-serve-<target>.tar.gz`,
`macacoview-v0.2.0-rc2-linux.tar.gz`), la version declarada en el repo es la del
tag, y el README no manda a bajar ni a compilar nada que no exista.

- [x] Version a `0.2.0-rc2` en los seis archivos (los cuatro declarados mas
      `package-lock.json`, que `npm install --package-lock-only` sincronizo de
      paso con el nombre con scope, y `gui/pc_ai_monitor/__init__.py`).
- [x] README: `cd pc-ai-monitor` -> `cd macacoview`; el ejemplo
      `--from-release v0.1.0` pasa a `v0.2.0-rc2`; la contradiccion "el
      repositorio es publico" / "el repositorio es privado" se resuelve por lo
      publico.
- [x] `install.sh`: `latest` resuelve contra la lista de releases, saltea
      borradores y acepta pre-releases, con error claro si no hay ninguna.
- [x] `release.yml`: la pata Intel pasa a `macos-15-intel` y el comentario que
      hablaba de repo privado deja de mentir.
- [x] Un tag con sufijo de pre-release se publica ya marcado como pre-release:
      la rc1 salio como release plena y hubo que corregirla a mano despues.
- [x] Publicado (`v0.2.0-rc2`, pre-release automatica) y verificado contra los
      assets reales, no contra el color del workflow: bajados de la release, los
      dos SHA256 dan OK, cada tar trae **un solo** archivo `macacoview-serve`, y
      `file` confirma `Mach-O 64-bit arm64` (2.192.688 bytes) y
      `Mach-O 64-bit x86_64` (2.454.448 bytes), los dos con permiso de ejecucion.
      La pata Intel existe de verdad por primera vez: el job
      `package-macos (x86_64-apple-darwin, macos-15-intel)` termino **success**,
      no cancelado tras 24 h.

## Linea 2: inventario de modelos instalados

Hoy solo existen los modelos **cargados**: servidores llama.cpp/vLLM/MLX
detectados por argv, mas los de Ollama via `ollama ps` (merge 3d8e4fe). No hay
ninguna nocion de "instalado" en el codigo.

Decisiones tomadas por el usuario:

- Fuentes: Ollama `GET /api/tags` y archivos `.gguf` en disco. La CLI de Ollama
  es un cliente de esa misma API, asi que no aporta un segundo dato.
- Presentacion: seccion "Instalados" **debajo** de las tarjetas de cargados,
  dentro del mismo panel de Modelos. Sin medidores: un modelo instalado y
  apagado no tiene GTT/RSS/CPU, y mostrar ceros seria mentir (mismo invariante
  que `Option<f64>` de GPU y que el panel de grupos).
- Alcance: solo el daemon y la UI web (el lado Rust). La app GTK de Linux queda
  fuera.

- [x] `Stats.model_inventory` con `#[serde(default)]`.
- [x] Colector en el lado Rust, en el seam de `collector.rs` (despues de
      `collect_stats`), para que Linux y macOS compartan una sola implementacion
      y los tests corran en Linux.
- [x] Directorios de disco por configuracion (`model_dirs`), no adivinados.
- [x] Cache con TTL de 60 s: `run_tick` llama `collect_stats()` **cada segundo**,
      asi que sin cache seria un GET HTTP y un barrido de disco por segundo.
- [x] UI: tabla de instalados, vacia cuando no hay nada (sin relleno).

Estado: **implementado, commiteado y publicado en `v0.2.0-rc3`** (commit
`1a9015f`). Archivos: nuevo `src-tauri/src/inventory.rs` (694 lineas, 15 tests)
mas `lib.rs`, `stats.rs`, `collector.rs`, `config.rs`, `macos.rs`,
`src/types.ts`, `src/components/Recursos.tsx`, `src/App.css` y `README.md`.

Desvio del brief, encontrado por el writer y aceptado: el campo no viaja solo en
`Stats` sino tambien en `FullSnapshot` + `apply()` de `collector.rs`. Sin eso el
literal manual habria descartado `model_inventory` antes de serializar, y la UI
nunca lo habria visto. Fue un hueco del brief, no del writer.

Revision del padre (yo), sobre el codigo y no sobre el reporte:
- `cargo test --offline`: **160 passed, 0 failed, 1 ignored**; `--lib` **161**
  (piso de CI 146).
- `cargo check --offline --all-targets`: exit 0.
- Clippy: el writer dejo 3 avisos en `inventory.rs` (tipo complejo en la cache,
  dos `&[x.clone()]` en tests). **Corregidos por mi** (alias `CachedInventory` y
  `std::slice::from_ref`); ahora `cargo clippy --all-targets` no reporta ninguna
  linea de `inventory.rs` (quedan 3 preexistentes en `tokens.rs` y `macos.rs`).
- `npm run build`: OK.

Dos hallazgos que valen mas que el codigo nuevo:

1. **La doc mentia sobre el presupuesto del barrido.** Dice "500 files in
   total", pero solo los `*.gguf` consumen el presupuesto: un directorio lleno de
   archivos ajenos se recorre entero. Corregi la doc para que diga lo que hace
   (el tope acota la lista, no el tiempo; lo que acota el tiempo es la
   profundidad y el TTL).
2. **`tokens.rs` tiene un test flaky preexistente**, y no es de este trabajo.
   `a_script_that_runs_and_fails_is_still_an_error` falla con `Text file busy
   (os error 26)` en `src/tokens.rs:1248`. `tokens.rs` esta **byte-identico a
   HEAD**. Medido aca: **3/25 corridas** de la suite completa fallan, y
   **2/25** fallan tambien salteando `--skip inventory::`, o sea que mis tests no
   lo causan (con solo los tests de `tokens` filtrados, 60 corridas, 0 fallas:
   necesita el paralelismo de la suite entera).
   Mecanismo: `write_executable_script` escribe un hermano y lo renombra
   creyendo que asi esquiva ETXTBSY, pero **el rename no cambia el inodo**. Si
   otra hebra hace `fork()` justo mientras el `fs::write` tiene el descriptor
   abierto, el hijo hereda ese descriptor de escritura y el inodo queda "abierto
   para escritura" mientras el hijo viva; el `exec` del padre sobre ese mismo
   inodo falla con ETXTBSY. El comentario del helper afirma lo contrario de lo
   que hace el kernel.

Primer intento fallido, para que no se repita: se delego el trabajo a un writer
en un worktree limpio y **murio a los 20 minutos y 102 turnos sin escribir un
solo archivo**. La causa no fue el diseno sino el interceptor de caveman: toda
salida de herramienta por encima de ~600 bytes vuelve como un handle `ccr://`,
asique el writer intentaba leer `config.rs` en tajadas de 600 bytes. El arbol
quedo limpio. La extension se desactivo (movida a `~/.pi/disabled/`); antes de
relanzar el writer, verificar que el interceptor ya no actua.

## Linea 3: probar el binario en una Mac ajena

Es lo unico que sigue sin verificar en la maquina de otra persona, y no se puede
hacer desde aca: necesita a alguien con una Mac.

- [x] `mac-verify.sh` y el runbook nombran el binario como es hoy
      (`macacoview-serve`), y el script busca en el directorio actual y en
      `~/Downloads` antes que en los `target/` de desarrollo.
- [x] Handoff con los pasos exactos en `odd/tasks/mac-handoff.md`: que Mac es,
      que asset bajar, `shasum -a 256 -c`, cuarentena, arrancar el daemon en el
      puerto 8787, correr el script y devolver el bloque `=== INFORME ===`. Con
      la lista de "esto NO es un bug" para que la persona no reporte el tablero
      vacio como un fallo. Falta que una persona lo corra.

## Evidencia recogida

- `cargo test --offline`: **145 passed, 0 failed, 1 ignored**.
- `cargo test --lib -- --list | grep -c ': test$'`: **146** (piso de CI: 91).
- `npm run build` (tsc + vite): OK, 28 modulos.
- `npm ci --offline` sobre el lock tocado, en un directorio aparte: OK, 29
  paquetes.
- `bash -n` en `gui/install.sh` y `scripts/mac-verify.sh`: OK.
- Gate de embedding, el mismo de `ci.yml` y de la pata Mac del release
  (`touch build.rs; cargo build`): `embedded 6 frontend asset(s) from dist/`.
- Python, `python3 -m unittest discover -s tests` desde `gui/`: **42 tests OK**.
- API de GitHub, medido: `releases/latest` -> 404, `releases?per_page=10` -> 200,
  y la resolucion nueva devuelve `v0.2.0-rc1`.
- Run del release `36856695274`, `WATCH_EXIT=0`: los tres jobs en **success**
  (`package`, `package-macos (aarch64-apple-darwin, macos-14)`,
  `package-macos (x86_64-apple-darwin, macos-15-intel)`), `prerelease=true`, y
  seis assets, todos con el nombre nuevo.
- Los 27 bloques `run:` de `release.yml` y `ci.yml`, renderizados desde el YAML
  y pasados por `bash -n`: ninguno falla. La logica de `--prerelease` probada
  con `set -euo pipefail` en bash 5.3: `v0.2.0-rc2` -> `[--prerelease]`,
  `v0.3.0` -> `[]`.
- rc3 (run `36867493679`, `WATCH_EXIT=0`): los tres jobs en **success**, otra vez
  con la pata Intel en `macos-15-intel`; `prerelease=true`; seis assets. Los dos
  binarios de Mac bajados y verificados: SHA256 OK, un solo `macacoview-serve`,
  `Mach-O 64-bit arm64` de 2.225.872 bytes y `Mach-O 64-bit x86_64` de 2.491.472
  bytes. Crecieron respecto de rc2 (2.192.688 y 2.454.448): es lo que se espera
  si el inventario va adentro del binario.
- Flaky de `tokens.rs` (`fae8281`): **3/25** corridas de la suite completa
  fallaban antes y **2/25** salteando los tests del inventario, o sea que ya
  estaba en `main`; despues del arreglo, **0/25**. Los dos scripts que los tests
  ejecutan son ahora fixtures versionados con modo `100755`, asi que en tiempo
  de test no se escribe ningun ejecutable y no queda ventana para ETXTBSY.

Lo que todavia no esta probado: la Mac de otra persona, que necesita otra
persona.