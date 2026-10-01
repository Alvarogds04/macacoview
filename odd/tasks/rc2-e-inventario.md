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
- [ ] Publicar el tag y verificar los assets con la salida real del run.

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

- [ ] `Stats.model_inventory` con `#[serde(default)]` (compatibilidad hacia
      atras con el script Python de Linux y con los fixtures).
- [ ] Colector en el lado Rust, en el seam de `collector.rs` (despues de
      `collect_stats`), para que Linux y macOS compartan una sola
      implementacion y los tests corran en Linux.
- [ ] Directorios de disco por configuracion (`model_dirs`), no adivinados.
- [ ] Cache con TTL: el barrido de disco no puede correr en cada tick.
- [ ] UI: tabla de instalados, vacia cuando no hay nada (sin relleno).

## Linea 3: probar el binario en una Mac ajena

Es lo unico que sigue sin verificar en la maquina de otra persona, y no se puede
hacer desde aca: necesita a alguien con una Mac.

- [x] `mac-verify.sh` y el runbook nombran el binario como es hoy
      (`macacoview-serve`), y el script busca en el directorio actual y en
      `~/Downloads` antes que en los `target/` de desarrollo.
- [ ] Handoff con los pasos exactos (bajar, verificar sha256, quitar cuarentena,
      arrancar, correr el script, que devolver).

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
- Los 27 bloques `run:` de `release.yml` y `ci.yml`, renderizados desde el YAML
  y pasados por `bash -n`: ninguno falla. La logica de `--prerelease` probada
  con `set -euo pipefail` en bash 5.3: `v0.2.0-rc2` -> `[--prerelease]`,
  `v0.3.0` -> `[]`.

Lo que todavia no esta probado: el run del release con la pata Intel en
`macos-15-intel`, porque eso solo se sabe despues de empujar el tag; y la Mac de
otra persona, que necesita otra persona.
