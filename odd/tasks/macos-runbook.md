# Runbook: verificar el colector de macOS en un Mac real

Todo lo que el CI sabe hoy viene del runner `macos-14`, que es una **VM**
(`VirtualMac2,1`, "Apple M1 (Virtual)"): la GPU que vio es `AppleParavirtGPU`,
el swap reporta `0.00M` en todo y no hay servidores de modelos. El fixture
`src-tauri/tests/fixtures/macos-capture.txt` es salida auténtica de esa VM.
Este runbook es lo que queda por confirmar en **silicio real**.

## 0. Estado del script (leer antes de correr)

La verificación es ahora **un comando**: `scripts/mac-verify.sh`, que se corre
en el Mac y escupe un informe listo para copiar y pegar de vuelta. El script
es **sólo lectura** (sysctl, sw_vers, vm_stat, ps, lsof, ioreg,
system_profiler, xattr, y un GET a /api/state): no escribe, no modifica, no
borra nada, y si falta una herramienta o un permiso lo dice y sigue.

**El script no se pudo probar en macOS: se escribió en Linux.** La primera vez
que corra va a ser en la máquina real; si algo truena o una sección sale
vacía de forma sospechosa, anotalo en el informe de vuelta — ese es justamente
el dato que falta.

Llevar: un Mac Apple Silicon, el binario sin firmar (§2.6), acceso al repo, y
(opcional, para re-capturar) `gh` autenticado.

## 1. Corré el script

```sh
./scripts/mac-verify.sh
```

Si `bash` se queja de permisos: `chmod +x scripts/mac-verify.sh` (una vez).
Opciones:

- `--with-sudo`: además corre `sudo lsof -nP -iTCP -sTCP:LISTEN` para comparar
  cuántas filas recorta TCC sin sudo. Sigue siendo sólo lectura, pero pide tu
  contraseña.
- `--api URL`: sondea esa URL para `/api/state` en vez de autodetectar el
  puerto en escucha del daemon.
- Una ruta al final: el binario del daemon (`macacoview-serve`). Sin ruta busca
  en el directorio actual, `~/Downloads` y `src-tauri/target/release|debug`.

Al final imprime un bloque `=== INFORME ===` y otro `=== LO QUE TODAVÍA NO
SABEMOS ===`: **copiá ambos de vuelta** al repo (issue, PR o este runbook).

## 2. Leé cada parte del informe

### 2.1 Identidad

`hw.model`, `hw.memsize`, `hw.pagesize`, marca de CPU, `sw_vers`. Confirmá:
`hw.model` **no** dice `Virtual...`, y `hw.pagesize` es **16384** (4096 en
Intel). El smoke test (`mod smoke` en `src-tauri/src/macos.rs`) afirma la
propiedad (potencia de dos, >= 4096), no el número.

### 2.2 GPU: AGXAccelerator vs AppleParavirtGPU

En la VM el nodo es `AppleParavirtGPU`; en silicio real se espera
`AGXAccelerator` (¿cambia por chip M1/M2/M3/M4? — anotalo). El informe imprime
el nodo, los diccionarios `PerformanceStatistics` deduplicados verbatim, y la
**comparación explícita `alloc` vs `in_use`**. Mirar:

- **Cuántos diccionarios distintos** hay (M Pro/Max/Ultra pueden exponer más
  de uno; el parser `parse_ioreg_gpu` suma los únicos y deduplica los
  repetidos verbatim, y no matchea nombres de dispositivo, sólo la clave
  `"PerformanceStatistics"`).
- **Claves**: la VM muestra `"Alloc system memory"`, `"In use system memory"`,
  `"In use system memory (driver)"` y `"recoveryCount"`. El script marca en
  "lo que todavía no sabemos" las que falten; fijate también si hay claves
  **nuevas** y contrastá valores con el gráfico GPU de Activity Monitor en el
  mismo momento.

### 2.3 La contradicción alloc vs in_use (decidir semántica)

En la captura del runner: `alloc = 39.108.608`, `in_use = 50.103.936` →
`in_use > alloc`, lo que contradice la intuición. Por eso ni el parser ni el
smoke afirman orden entre los dos. El informe compara los dos números y deja
la relación anotada. Para decidir la semántica de verdad, capturá los pares en
reposo **y bajo carga Metal real** (un modelo en Ollama, o Activity Monitor
corriendo algo pesado) — re-corre el script en ambos momentos y compará los
dos informes. Con eso se decide si la UI muestra "asignado" o "en uso", y si
`(driver)` suma o no.

### 2.4 vm_stat y tamaño de página

El informe incluye el volcado completo, el tamaño de página del encabezado
(`page size of ... bytes`) y `hw.pagesize`. En el fixture el encabezado dice
16384; el parser prefiere el del encabezado y cae a `hw.pagesize` si no está.
Si el número efectivo no es 16384, el script lo marca. También imprime
`sysctl vm.swapusage`: en la VM era todo `0.00M` y `parse_swap` nunca vio swap
ocupado de verdad.

### 2.5 Motores de modelos

El script busca en `ps -ww -axo pid=,args=` procesos de `llama-server`,
`vllm`, `mlx` y `ollama`, y dice qué encontró y qué no. Ojo: Ollama corre el
modelo en un hijo **`ollama runner`** — si `ollama` aparece, fijate si el hijo
está en la lista: el colector debe ver ambos. Si aparece un motor hoy ausente
en el runner, capturale fixture propio (`ollama ps` incluido).

### 2.6 Puertos en escucha (lsof y TCC)

El informe imprime `lsof -nP -iTCP -sTCP:LISTEN` **sin sudo** y anota cuántas
filas dio. Con TCC restrictivo o sin sudo, `lsof` puede devolver **menos filas
sin fallar**: eso no es un bug del colector. Con `--with-sudo` el script corre
también la versión con sudo y marca si recortó filas. El colector nunca debe
entrar en panic por esto; la tabla de puertos puede quedar más corta.

### 2.7 Binario sin firmar y daemon

Si encuentra el binario, imprime `xattr -l`. Si `com.apple.quarantine` está
presente y Gatekeeper lo bloquea al abrirlo: clic derecho sobre el binario ->
**Abrir** (la primera vez), o quitá la cuarentena a mano:

```sh
xattr -d com.apple.quarantine /ruta/al/binario
```

Si el daemon está corriendo, el script sondea `/api/state` en el puerto que
tenga en escucha (o en `--api URL`) y reporta si responde.

El daemon se compila y se arranca así (el binario es `macacoview-serve`):

```sh
cd <repo>/src-tauri && cargo build --release --bin macacoview-serve
PC_AI_PORT=8787 ./target/release/macacoview-serve
```

El puerto por defecto es **8787** y escucha **sólo en `127.0.0.1`**: no queda
expuesto a la red. Ojo con un caso que ya nos mordió: si el frontend no fue
compilado, el servidor **igual responde 200** con una página que avisa que falta.
Por eso el informe tiene que decir si ves la interfaz de verdad o ese aviso, y no
dar por bueno un 200 pelado.

## 3. Pegar el informe de vuelta

Copiá los bloques `=== INFORME ===` y `=== LO QUE TODAVÍA NO SABEMOS ===` a la
issue/PR de verificación. Con el informe en el repo:

1. Compará cada sección contra `src-tauri/tests/fixtures/macos-capture.txt`.
2. Si una sección cambió de formato o muestra claves nuevas, escribir/ajustar
   el parser **contra ese texto** y pegar las líneas relevantes en el fixture
   antes de tocar código. El fixture es la fuente de verdad de los parsers.
3. Correr `cargo test --lib` en el Mac: los tests de `mod smoke`
   (`src-tauri/src/macos.rs`) ejecutan `collect_*` contra esta máquina, no
   contra texto capturado.

Extra (opcional, para re-capturar la salida del runner):

```sh
gh workflow run macos-capture.yml
gh run list --workflow=macos-capture.yml --limit 1   # tomar el id
gh run view --log <id> > nueva-captura.txt
```

## 4. Qué NO está verificado todavía

No dar por hecho nada de esta lista:

- **Semántica de `Alloc system memory` vs `In use system memory`** en silicio
  real (§2.3): el parser no afirma orden entre ellos, y en la VM `in_use >
  alloc`.
- **Claves del diccionario en silicio real**: todo lo sabido de GPU viene de
  `AppleParavirtGPU`; `AGXAccelerator` puede exponer más (o menos) claves.
- **La disjunción de categorías de `vm_stat`**: `used + available <= total`
  no se afirma ni en el smoke, porque no se pudo verificar en hardware real.
- **Swap real**: la captura muestra `total = 0.00M used = 0.00M free = 0.00M`;
  `parse_swap` nunca vio swap ocupado de verdad.
- **lsof/TCC en una Mac con apps reales**: la VM tenía sólo los puertos del
  runner; no se sabe cuánto recorta TCC en una máquina de uso diario.
- **Detección de servidores de modelos**: `ollama`, `llama-server` y `vllm`
  están ausentes en el runner; su salida real no tiene fixture.
- **`phys_footprint` vs `rss` bajo carga Metal**: sin medir en hardware, no se
  sabe cuánto subestima `rss` la memoria de GPU por proceso.
- **El propio `scripts/mac-verify.sh`**: escrito en Linux, sin probar nunca en
  macOS. La primera corrida real es también su prueba; cualquier sección
  vacía, mensaje raro o crash es un hallazgo que reportar.
