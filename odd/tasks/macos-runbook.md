# Runbook: verificar el colector de macOS en un Mac real

Todo lo que el CI sabe hoy viene del runner `macos-14`, que es una **VM**
(`VirtualMac2,1`, "Apple M1 (Virtual)"): la GPU que vio es `AppleParavirtGPU`,
el swap reporta `0.00M` en todo y no hay servidores de modelos. El fixture
`src-tauri/tests/fixtures/macos-capture.txt` es salida auténtica de esa VM.
Este runbook es lo que queda por confirmar en **silicio real**, con los
comandos exactos y qué anotar de cada uno.

Llevar: un Mac Apple Silicon, el binario sin firmar (§5), acceso al repo, y
(opcional, para re-capturar) `gh` autenticado.

## 1. Confirmar la GPU real

En la VM el nodo es `AppleParavirtGPU`. En silicio real se espera
`AGXAccelerator`, y puede exponer claves que en la captura no existen.

```sh
ioreg -r -d 1 -w 0 -c AGXAccelerator
ioreg -l -w 0 | grep -i -B2 -A3 PerformanceStatistics
```

Anotar:

- **Nombre exacto del nodo/clase** que expone el diccionario (¿es
  `AGXAccelerator`? ¿cambia por chip M1/M2/M3/M4?). El parser
  (`parse_ioreg_gpu` en `src-tauri/src/macos.rs`) no matchea nombres de
  dispositivo, sólo la clave `"PerformanceStatistics"`, pero el nombre hay que
  dejarlo anotado para el runbook siguiente.
- **Cuántos nodos IOAccelerator aparecen** y cuántos diccionarios
  *distintos* hay. M Pro/Max/Ultra pueden exponer más de uno; el parser suma
  diccionarios únicos y deduplica los repetidos verbatim.
- **Todas las claves del diccionario.** La VM muestra `"Alloc system memory"`,
  `"In use system memory"`, `"In use system memory (driver)"` y
  `"recoveryCount"`. Silicio real puede traer más; verificar que la pareja de
  bytes sigue presente y con qué claves extra convive.
- **Valores y unidades** (se esperan bytes; contrastar con el gráfico GPU de
  Activity Monitor en el mismo momento).

## 2. La contradicción alloc vs in_use (decidir semántica)

En la captura del runner:

```
"PerformanceStatistics" = {"Alloc system memory"=39108608,"In use system memory"=50103936,...}
alloc = 39.108.608   in_use = 50.103.936   ->  in_use > alloc
```

Eso **contradice la intuición**: uno esperaría que lo "en uso" quepa dentro de
lo "asignado". Por eso ni el parser ni el smoke test de `mod smoke` afirman
orden entre los dos contadores. En silicio real hay que decidir cuál es la
semántica correcta de cada contador:

```sh
ioreg -l -w 0 | grep -o '"Alloc system memory"=[0-9]*' | head
ioreg -l -w 0 | grep -o '"In use system memory"=[0-9]*' | head
```

Capturar los pares en reposo y bajo carga Metal real (por ejemplo, un modelo
en Ollama o el gráfico GPU de Activity Monitor corriendo algo pesado), y
anotar si la relación se mantiene o se invierte. Con eso se decide si el
nombre que muestra la UI es "asignado" o "en uso", y si `(driver)` suma o no.

## 3. Tamaño de página y vm_stat

```sh
sysctl -n hw.memsize hw.pagesize
vm_stat
```

- `hw.pagesize` esperado: **16384** en Apple Silicon (4096 en Intel). El
  assert del smoke es la propiedad (potencia de dos, >= 4096), no el número.
- Comparar el volcado de `vm_stat` contra el formato del fixture
  (`src-tauri/tests/fixtures/macos-capture.txt`): encabezado
  `Mach Virtual Memory Statistics: (page size of ... bytes)` y contadores con
  punto final. El parser no debe depender del encabezado para el tamaño de
  página (prefiere `vm_stat` sólo si imprime el suyo, si no `hw.pagesize`).
- Correr `cargo test --lib` en el Mac: los tests de `mod smoke`
  (`src-tauri/src/macos.rs`) ejecutan `collect_*` contra esta máquina, no
  contra texto capturado.

## 4. Permisos: lsof y TCC

```sh
lsof -nP -iTCP -sTCP:LISTEN
sudo lsof -nP -iTCP -sTCP:LISTEN
```

Con TCC restrictivo o sin sudo, `lsof` puede **devolver menos filas sin
fallar**. Eso no es un bug del colector: anotar cuántas filas da cada
invocación y en qué condiciones. El colector nunca debe entrar en panic por
esto; la tabla de puertos puede quedar más corta.

## 5. Binario sin firmar

El binario va **sin firmar ni notarizar**. Si Gatekeeper lo bloquea al abrirlo:

```sh
xattr -d com.apple.quarantine /ruta/al/binario
```

o, sin terminal: clic derecho sobre el binario -> **Abrir** (la primera vez).

## 6. Volver a capturar la salida del runner

```sh
gh workflow run macos-capture.yml
gh run list --workflow=macos-capture.yml --limit 1   # tomar el id
gh run view --log <id> > nueva-captura.txt
```

Qué hacer con la salida:

1. Comparar cada sección contra `src-tauri/tests/fixtures/macos-capture.txt`.
2. Si una sección cambió de formato o muestra claves nuevas, escribir/ajustar
   el parser **contra ese texto** y pegar las líneas relevantes en el fixture
   antes de tocar código. El fixture es la fuente de verdad de los parsers.
3. Si aparece un servidor de modelos en el runner (hoy no hay ninguno),
   capturar `ollama ps` y dedicarle fixture propio.

## 7. Qué NO está verificado todavía

No dar por hecho nada de esta lista:

- **Semántica de `Alloc system memory` vs `In use system memory`** en silicio
  real (§2): el parser no afirma orden entre ellos, y en la VM `in_use >
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
