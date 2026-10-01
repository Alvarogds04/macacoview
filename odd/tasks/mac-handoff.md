# Handoff: probar el daemon en una Mac ajena

Estado: **pendiente de ejecutar**. Es lo unico del cierre de MacacoView que no se
puede verificar desde esta maquina; necesita a alguien con una Mac (cualquiera:
la tuya, ya se probo; la de otra persona, no).

Version a probar: **v0.2.0-rc2**, pre-release.
Binarios verificados antes de escribir esto: descargados de la release, SHA256
correcto, cada tar trae un solo archivo `macacoview-serve`,
`Mach-O 64-bit arm64` (2.192.688 bytes) y `Mach-O 64-bit x86_64` (2.454.448
bytes), los dos con permiso de ejecucion.

Repositorio: `https://github.com/Alvarogds04/macacoview` (publico: no hace falta
ninguna cuenta ni token).
Release: `https://github.com/Alvarogds04/macacoview/releases/tag/v0.2.0-rc2`

## Texto para pasarle a la persona

Copiar tal cual. Esta escrito para alguien que no programa.

---

Necesito que corras algo en tu Mac y me pases el resultado. Son dos partes: bajar
el programa (una vez) y correr un informe que lee datos de tu maquina. **Nada se
instala, nada se modifica**: el informe solo lee. Si algo no te sale, corta y
escribime; no vale tratar de arreglarlo.

**Parte 1: bajar el programa.**

Primero necesito saber que Mac tenes. Menu Apple (la manzanita, arriba a la
izquierda) -> **Acerca de este Mac**:

- Si dice **Chip: Apple M1 / M2 / M3 / M4** (o M seguido de un numero), sos Apple
  Silicon. El archivo que necesitas termina en **`aarch64-apple-darwin`**.
- Si dice **Procesador: Intel ...**, el archivo termina en
  **`x86_64-apple-darwin`**.

No son intercambiables: si bajas el otro, no arranca.

Anda a la pagina de la release (link arriba), seccion **Assets**, y baja DOS
archivos de tu arquitectura: el `.tar.gz` y su `.sha256`. Los dos van a
`Descargas`.

Despues abri **Terminal** (con Spotlight: Command + Espacio, escribi
"Terminal") y pega estos comandos, cambiando `aarch64` por `x86_64` si tu Mac es
Intel. Uno por uno:

```sh
cd ~/Downloads
```

```sh
shasum -a 256 -c macacoview-serve-aarch64-apple-darwin.tar.gz.sha256
```

Tiene que decir **OK**. Si dice FAILED, corta ahi y avisame: significa que la
descarga vino mal y no hay que seguir.

```sh
tar -xzf macacoview-serve-aarch64-apple-darwin.tar.gz
```

Esto deja un archivo llamado `macacoview-serve` en Descargas. Es el programa.

**Parte 2: correrlo.**

El programa no esta firmado por Apple (es una version de prueba y la firma se
paga), asi que macOS lo va a frenar la primera vez. Se destraba asi:

```sh
xattr -d com.apple.quarantine ~/Downloads/macacoview-serve
```

(La otra forma, si preferis el mouse: clic derecho sobre el archivo -> **Abrir**,
y confirmar.) Ahora si:

```sh
~/Downloads/macacoview-serve
```

**Dejalo corriendo.** Va a quedar ocupando esa ventana del Terminal, y eso es
correcto. Abri el navegador en:

```text
http://127.0.0.1:8787
```

Tiene que aparecer un tablero. NO importa que quede casi vacio (ver abajo). Deja
esa ventana del Terminal abierta y volve a la otra.

**Parte 3: el informe.**

En la ventana de Terminal que te quede libre, pega:

```sh
cd ~/Downloads
curl -fsSL https://raw.githubusercontent.com/Alvarogds04/macacoview/main/scripts/mac-verify.sh -o mac-verify.sh
```

```sh
bash mac-verify.sh ~/Downloads/macacoview-serve
```

Puede tardar un minuto. Va a imprimir mucho texto. **Pasame todo lo que sale
desde la linea que dice `=== INFORME ===` hasta el final**, copiado y pegado tal
cual. Si te pregunta por la contrasena, decile que no: el script no la necesita
(con `--with-sudo` la pediria, pero no lo usamos).

**Que NO es un problema** (no me lo reportes como error):

- El tablero vacio o casi vacio. En una Mac recien instalada no hay nada que
  medir de modelos ni de tokens, y eso es correcto, no un fallo.
- Que no aparezca ningun modelo local: aparece solo si hay un servidor de
  modelos corriendo (por ejemplo Ollama).
- Que la memoria de GPU diga "—" o "no medible". En Apple Silicon la memoria es
  compartida y macOS no publica cuanto usa cada proceso: mostrar 0 seria mentir.
- Que la lista de token diga que no hay datos si no usas Claude Code ni Codex.

**Si algo falla**, lo que me sirve es: el comando exacto que corriste, el texto
de error completo, y si el tablero abrio o no.

---

## Que hago yo cuando vuelve

1. Leer el bloque `=== INFORME ===` seccion por seccion (identidad, GPU, vm_stat,
   motores de modelos, puertos, cuarentena y `/api/state`).
2. Comparar contra lo medido en el runner `macos-14` de CI y contra el bloque
   "lo que todavia no sabemos" del runbook: lo que este ahi y el informe
   confirme, se cierra; lo que el informe contradiga, es un bug con evidencia.
3. Anotar en `odd/tasks/macos-runbook.md` lo que quedo medido, con la maquina y
   la version entre parentesis.

## Cosas que el informe no va a probar

- Que el binario funcione en **otra** Mac distinta de esa (una prueba, una
  maquina). Con Apple Silicon e Intel ya tenemos las dos arquitecturas
  compiladas, pero no probadas a mano por una persona.
- Nada sobre `sudo`/TCC mas alla de lo que el script lee sin permisos.
- Si el puerto 8787 estaba ocupado por otra cosa, el daemon falla al arrancar;
  el informe lo va a mostrar como "no responde" y se distingue por el mensaje de
  error del Terminal. Con `PC_AI_PORT=8790 ~/Downloads/macacoview-serve` corre en
  otro puerto (la variable conserva el prefijo viejo a proposito: renombrarla
  romperia lo que ya este instalado).
