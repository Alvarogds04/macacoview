#!/bin/sh
# mac-verify.sh — verificación read-only del colector de macOS en un Mac real.
#
# Compañero de odd/tasks/macos-runbook.md: correrlo en la Mac, y pegar el
# bloque "=== INFORME ===" de vuelta en el repo.
#
# SOLO LECTURA. Todos los comandos que corre son lecturas (sw_vers, sysctl,
# vm_stat, ps, lsof, ioreg, system_profiler, xattr, y un GET con curl para
# /api/state). No escribe, no modifica, no borra nada. El único sudo posible
# es opcional (--with-sudo) y también es una lectura (tabla de sockets).
#
# Si falta una herramienta o un permiso, lo dice en el informe y sigue: un
# informe parcial vale más que ningún informe.
#
# Uso: ./mac-verify.sh [--with-sudo] [--api URL] [/ruta/al/binario]
#   --with-sudo   además corre `sudo lsof -nP -iTCP -sTCP:LISTEN` para
#                 comparar cuántas filas recorta TCC sin sudo (pide
#                 contraseña; sigue siendo sólo lectura).
#   --api URL     URL a sondear para /api/state en vez de autodetectar el
#                 puerto en escucha del daemon.
#   /ruta/...     binario compilado del proyecto; si falta, se buscan las
#                 rutas habituales.

set -u

PROG=$(basename "$0")

usage() {
    cat <<EOF
Uso: $PROG [--with-sudo] [--api URL] [/ruta/al/binario]
  --with-sudo   comparación opcional de lsof con sudo (sólo lectura).
  --api URL     sondear esta URL para /api/state en vez de autodetectar.
EOF
}

# ------------------------------------------------------------------ guard ----
if [ "$(uname -s)" != "Darwin" ]; then
    cat >&2 <<EOF
$PROG: este script debe correr en macOS: inspecciona estado de un Mac real
(sw_vers, sysctl, ioreg, vm_stat, lsof, xattr). Aquí el sistema es
"$(uname -s) $(uname -r) ($(uname -m))".
No se leyó ni modificó nada. Corrélo en el Mac de destino y pegá el bloque
"=== INFORME ===" de vuelta en el repo (ver odd/tasks/macos-runbook.md).
EOF
    exit 2
fi

# ---------------------------------------------------------------- helpers ----
UNKNOWNS=""
NOTE_SEP=""

note() {
    UNKNOWNS="$UNKNOWNS$NOTE_SEP- $1"
    NOTE_SEP="
"
}

progress() {
    printf '[%s] %s\n' "$1" "$2" >&2
}

REPORT=""

emit() {
    REPORT="$REPORT
$*"
}

section() {
    emit ""
    emit "--- $* ---"
}

# try <tool> [args...] — corre la herramienta, o imprime un marcador claro y
# devuelve 1 si no existe. Pensado para: out=$(try sw_vers) || note "..."
try() {
    if command -v "$1" >/dev/null 2>&1; then
        "$@" 2>&1
        return 0
    fi
    echo "[FALTA] '$1' no está disponible en este sistema; esta parte del informe queda vacía."
    return 1
}

# ------------------------------------------------------------------ args -----
with_sudo=0
api_url=""
bin_arg=""
while [ $# -gt 0 ]; do
    case "$1" in
        --with-sudo) with_sudo=1 ;;
        --api) [ $# -ge 2 ] || { echo "$PROG: --api necesita una URL" >&2; exit 2; }; api_url=$2; shift ;;
        --api=*) api_url=${1#--api=} ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "$PROG: opción desconocida: $1" >&2; usage >&2; exit 2 ;;
        *) bin_arg=$1 ;;
    esac
    shift
done

# Variables compartidas entre secciones
hw_pagesize=""
agx_out=""
perf_raw=""

# ============================================================== 1. identidad ==
progress 1/7 "identidad (sw_vers, sysctl, uname, system_profiler)"
section "1. Identidad"
out=$(try sw_vers) && emit "$out" || note "sw_vers ausente: versión de macOS sin confirmar."
emit "-- uname -a --"
emit "$(uname -a)"
if command -v sysctl >/dev/null 2>&1; then
    hw_model=$(sysctl -n hw.model 2>&1)
    hw_memsize=$(sysctl -n hw.memsize 2>&1)
    hw_pagesize=$(sysctl -n hw.pagesize 2>&1)
    cpu_brand=$(sysctl -n machdep.cpu.brand_string 2>&1)
    emit "-- sysctl --"
    emit "hw.model                 = $hw_model"
    emit "hw.memsize               = $hw_memsize"
    emit "hw.pagesize              = $hw_pagesize"
    emit "machdep.cpu.brand_string = $cpu_brand"
    case "$hw_model" in
        *Virtual*) emit "AVISO: hw.model sugiere una MÁQUINA VIRTUAL, no silicio real." ;;
    esac
    if [ "$hw_pagesize" != "16384" ]; then
        note "hw.pagesize = $hw_pagesize, no los 16384 esperados en Apple Silicon (4096 en Intel)."
    fi
else
    note "sysctl ausente: identidad de hardware y tamaño de página sin confirmar."
fi
emit "-- system_profiler SPHardwareDataType --"
out=$(try system_profiler SPHardwareDataType) \
    && emit "$out" \
    || note "system_profiler no disponible o falló: sin ficha de hardware."

# =============================================================== 2. GPU =======
progress 2/7 "GPU (AGXAccelerator vs AppleParavirtGPU, PerformanceStatistics)"
section "2. GPU: nodo IOAccelerator y PerformanceStatistics"
if command -v ioreg >/dev/null 2>&1; then
    agx_out=$(ioreg -r -d 1 -w 0 -c AGXAccelerator 2>/dev/null)
    pvirt_out=$(ioreg -r -d 1 -w 0 -c AppleParavirtGPU 2>/dev/null)
    if [ -n "$agx_out" ]; then
        emit "Nodo AGXAccelerator: PRESENTE (silicio real, lo que el runbook espera)."
        emit "$agx_out"
    else
        emit "Nodo AGXAccelerator: ausente."
        note "AGXAccelerator no encontrado: ¿es esta máquina silicio real? ¿cambia el nombre por chip (M1/M2/M3/M4)?"
    fi
    if [ -n "$pvirt_out" ]; then
        emit "Nodo AppleParavirtGPU: PRESENTE (máquina virtual, como el runner macos-14)."
        emit "$pvirt_out"
    else
        emit "Nodo AppleParavirtGPU: ausente."
    fi

    perf_raw=$(ioreg -l -w 0 2>/dev/null | grep -i '"PerformanceStatistics"')
    dedup=$(printf '%s\n' "$perf_raw" | sed 's/^[ |]*//' | sort -u)
    emit "-- diccionarios PerformanceStatistics (deduplicados verbatim) --"
    if [ -n "$dedup" ]; then
        emit "$dedup"
        n_perf=$(printf '%s\n' "$dedup" | grep -c .)
        emit "Diccionarios distintos: $n_perf (el parser suma los únicos y deduplica los repetidos)."
    else
        emit "No se encontró ningún diccionario PerformanceStatistics."
        note "PerformanceStatistics ausente en ioreg -l: sin datos de GPU para el parser."
    fi

    emit "-- comparación explícita alloc vs in_use --"
    alloc=$(printf '%s\n' "$perf_raw" | grep -o '"Alloc system memory"=[0-9]*' | head -1 | sed 's/.*=//')
    inuse=$(printf '%s\n' "$perf_raw" | grep -o '"In use system memory"=[0-9]*' | head -1 | sed 's/.*=//')
    driver=$(printf '%s\n' "$perf_raw" | grep -o '"In use system memory (driver)"=[0-9]*' | head -1 | sed 's/.*=//')
    if [ -n "$alloc" ] && [ -n "$inuse" ]; then
        emit "alloc     (\"Alloc system memory\")        = $alloc"
        emit "in_use    (\"In use system memory\")        = $inuse"
        if [ -n "$driver" ]; then
            emit "in_use_d  (\"In use system memory (driver)\") = $driver"
        else
            emit "in_use_d  (\"In use system memory (driver)\"): clave ausente."
        fi
        if [ "$inuse" -gt "$alloc" ]; then
            emit "Relación: in_use > alloc (igual que en la captura de la VM)."
            note "En esta máquina in_use > alloc: la contradicción de la captura del runner se reproduce en hardware real; la semántica de cada contador sigue sin decidirse."
        elif [ "$inuse" -eq "$alloc" ]; then
            emit "Relación: in_use == alloc."
            note "in_use == alloc en hardware real: contrastar con la VM (allá in_use > alloc) antes de decidir la semántica de la UI."
        else
            emit "Relación: in_use < alloc."
            note "in_use < alloc en hardware real: contraste con la VM, donde in_use > alloc."
        fi
    else
        emit "No se pudieron extraer 'Alloc system memory' / 'In use system memory'."
        note "El diccionario de GPU no trajo contadores alloc/in_use extraíbles: el parser (parse_ioreg_gpu) quedaría sin pareja de bytes."
    fi
    if [ -n "$agx_out" ]; then
        for k in "Alloc system memory" "In use system memory" "In use system memory (driver)" "recoveryCount"; do
            printf '%s' "$agx_out" | grep -q "\"$k\"" || \
                note "AGXAccelerator no expone '$k' (en la VM sí): las claves del silicio real difieren de la captura."
        done
        note "Queda por mirar a mano si el AGX trae claves NUEVAS más allá de las 4 de la VM (están impresas arriba)."
    fi
else
    note "ioreg ausente: sección de GPU (AGX vs Paravirt, PerformanceStatistics) sin datos."
fi

# ============================================================== 3. vm_stat ====
progress 3/7 "vm_stat y tamaño de página"
section "3. vm_stat y tamaño de página"
if command -v vm_stat >/dev/null 2>&1; then
    vm=$(vm_stat 2>&1)
    emit "$vm"
    header_size=$(printf '%s\n' "$vm" | sed -n 's/.*page size of \([0-9]*\) bytes.*/\1/p' | head -1)
    emit "-- tamaño de página efectivo --"
    emit "esperado en Apple Silicon = 16384"
    emit "hw.pagesize               = ${hw_pagesize:-desconocido}"
    if [ -n "$header_size" ]; then
        emit "encabezado de vm_stat     = $header_size (el parser prefiere este valor)"
        if [ "$header_size" != "16384" ]; then
            note "vm_stat reporta páginas de $header_size bytes, no los 16384 esperados."
        fi
    else
        emit "encabezado de vm_stat     = sin tamaño de página (el parser caería a hw.pagesize)"
        note "vm_stat no imprimió su tamaño de página en el encabezado: el parser depende de hw.pagesize."
    fi
else
    note "vm_stat ausente: volcado de memoria y tamaño de página efectivo sin confirmar."
fi
emit "-- sysctl vm.swapusage (en la VM era todo 0.00M) --"
if command -v sysctl >/dev/null 2>&1; then
    swap=$(sysctl -n vm.swapusage 2>&1)
    emit "$swap"
    case "$swap" in
        *"used = 0.00M"*) note "swap sin uso en esta corrida: parse_swap sigue sin ver swap ocupado de verdad." ;;
    esac
fi

# ====================================================== 4. motores de modelos ==
progress 4/7 "motores de modelos (ps)"
section "4. Motores de modelos en ejecución (ps)"
if command -v ps >/dev/null 2>&1; then
    psout=$(ps -ww -axo pid=,args= 2>/dev/null)
    if [ -n "$psout" ]; then
        found_any=0
        for pat in llama-server vllm mlx ollama; do
            hits=$(printf '%s\n' "$psout" | grep -i -- "$pat" || true)
            if [ -n "$hits" ]; then
                found_any=1
                emit "-- '$pat': PRESENTE --"
                emit "$hits"
            else
                emit "-- '$pat': no se encontró --"
            fi
        done
        emit "(Ollama corre el modelo en un hijo 'ollama runner': si 'ollama' aparece, fijate si el hijo está en la lista; el colector debe ver ambos.)"
        if [ "$found_any" = 0 ]; then
            note "Ningún motor de modelos corriendo en esta corrida: la salida real de ollama/llama-server/vllm sigue sin fixture."
        fi
    else
        emit "ps no devolvió procesos."
        note "ps no devolvió procesos: detección de motores sin datos."
    fi
else
    note "ps ausente: detección de motores de modelos sin datos."
fi

# ============================================================ 5. puertos ======
progress 5/7 "puertos en escucha (lsof)"
section "5. Puertos en escucha (lsof)"
if command -v lsof >/dev/null 2>&1; then
    lsof_out=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null)
    if [ -n "$lsof_out" ]; then
        rows=$(printf '%s\n' "$lsof_out" | grep -c .)
        emit "lsof SIN sudo: $rows líneas (la primera es la cabecera)."
        emit "$lsof_out"
        emit "NOTA: sin sudo, lsof puede devolver MENOS filas sin fallar (TCC); una tabla corta no es un bug."
    else
        emit "lsof SIN sudo no devolvió filas (o no hay listeners visibles)."
        note "lsof sin sudo devolvió 0 filas: no se sabe cuánto recortó TCC en esta máquina."
    fi
    if [ "$with_sudo" = 1 ]; then
        if command -v sudo >/dev/null 2>&1; then
            emit "-- comparación CON sudo (sólo lectura; pedís tu contraseña) --"
            sudo_out=$(sudo lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null)
            if [ -n "$sudo_out" ]; then
                sudo_rows=$(printf '%s\n' "$sudo_out" | grep -c .)
                emit "lsof CON sudo: $sudo_rows líneas."
                emit "$sudo_out"
                if [ "${rows:-0}" -lt "${sudo_rows:-0}" ]; then
                    note "lsof con sudo mostró más puertos que sin sudo: TCC recortó filas sin sudo."
                fi
            else
                emit "lsof CON sudo tampoco devolvió filas."
            fi
        else
            emit "sudo no disponible; se omite la comparación."
            note "sudo ausente: no se pudo comparar lsof con y sin sudo."
        fi
    else
        emit "(Para la comparación con sudo, corré: $PROG --with-sudo — sigue siendo sólo lectura.)"
    fi
else
    note "lsof ausente: puertos en escucha sin confirmar."
fi

# =================================================== 6. binario y daemon ======
progress 6/7 "binario del proyecto (cuarentena) y daemon /api/state"
section "6. Binario del proyecto (cuarentena) y daemon /api/state"
bin=""
if [ -n "$bin_arg" ]; then
    if [ -f "$bin_arg" ]; then
        bin=$bin_arg
    else
        emit "El binario indicado no existe: $bin_arg"
    fi
else
    for c in \
        ./src-tauri/target/release/pc-ai-monitor \
        ./src-tauri/target/debug/pc-ai-monitor \
        ./target/release/pc-ai-monitor \
        "$HOME/Downloads/pc-ai-monitor" \
        /Applications/pc-ai-monitor.app/Contents/MacOS/pc-ai-monitor
    do
        if [ -f "$c" ]; then
            bin=$c
            break
        fi
    done
fi

if [ -n "$bin" ]; then
    emit "Binario: $bin"
    if command -v xattr >/dev/null 2>&1; then
        emit "-- xattr -l (extensiones del binario) --"
        xa=$(xattr -l "$bin" 2>&1)
        if [ -n "$xa" ]; then
            emit "$xa"
        else
            emit "<sin extensiones>"
        fi
        if xattr -p com.apple.quarantine "$bin" >/dev/null 2>&1; then
            emit "com.apple.quarantine: PRESENTE (Gatekeeper puede bloquearlo)."
            emit "Si lo bloquea: clic derecho -> Abrir (la primera vez), o quitá la cuarentena a mano:"
            emit "  xattr -d com.apple.quarantine \"$bin\""
        else
            emit "com.apple.quarantine: ausente (no debería ser bloqueado por Gatekeeper)."
        fi
    else
        note "xattr ausente: estado de cuarentena del binario sin confirmar."
    fi

    bin_base=$(basename "$bin")
    procs=$(ps -ww -axo pid=,args= 2>/dev/null | grep -F -- "$bin_base" | grep -v grep || true)
    if [ -n "$procs" ]; then
        emit "-- daemon: PRESENTE --"
        emit "$procs"
        pid=$(printf '%s\n' "$procs" | head -1 | awk '{print $1}')
        dports=""
        if command -v lsof >/dev/null 2>&1; then
            dports=$(lsof -nP -a -p "$pid" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 { sub(".*:", "", $9); print $9 }' | sort -u)
        fi
        probe() {
            url=$1
            if command -v curl >/dev/null 2>&1; then
                if body=$(curl -fsS --max-time 3 "$url" 2>&1); then
                    body=$(printf '%s' "$body" | head -c 500)
                    emit "GET $url -> RESPONDE:"
                    emit "$body"
                else
                    emit "GET $url -> sin respuesta: $body"
                    note "El daemon está corriendo pero $url no respondió."
                fi
            else
                emit "curl no disponible; no se pudo sondear $url."
                note "curl ausente: /api/state sin verificar aunque el daemon corre."
            fi
        }
        if [ -n "$api_url" ]; then
            probe "$api_url"
        elif [ -n "$dports" ]; then
            for p in $dports; do
                probe "http://127.0.0.1:$p/api/state"
            done
        else
            emit "El daemon corre pero no tiene ningún puerto TCP en escucha (según lsof)."
            note "Daemon corriendo sin puerto detectable: /api/state sin verificar (pasá --api URL si conocés la URL)."
        fi
    else
        emit "El daemon no está corriendo; /api/state no se puede sondear (levantalo y re-corre el script)."
    fi
else
    emit "No se encontró el binario 'pc-ai-monitor' en las rutas habituales."
    emit "Pasalo explícito: $PROG /ruta/al/binario"
    note "Binario del proyecto no encontrado: cuarentena y /api/state sin verificar."
fi

# =============================================================== informe =====
progress 7/7 "armado del informe"
emit ""
emit "=== INFORME ==="
emit "máquina: $(uname -m), $(sw_vers -productName 2>/dev/null || echo macOS) $(sw_vers -productVersion 2>/dev/null || uname -sr), $(date '+%Y-%m-%d %H:%M:%S %Z')"
emit "generado por $PROG (sólo lectura). Copiá todo este bloque de vuelta al repo."
emit ""
emit ""
emit "=== LO QUE TODAVÍA NO SABEMOS ==="
if [ -n "$UNKNOWNS" ]; then
    emit "$UNKNOWNS"
else
    emit "(esta corrida no dejó nada pendiente marcado; igual leé cada sección contra odd/tasks/macos-runbook.md antes de dar algo por verificado.)"
fi
printf '%s\n' "$REPORT"
