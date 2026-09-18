# Feature: Recursos gráfico

## Objetivo

Convertir la pestaña **Recursos** de 3 tablas + resumen de texto en un panel gráfico
(barras grandes + tarjetas con medidores), sin agregar dependencias nuevas.

## Decisiones del usuario

- Estilo: **barras grandes + tarjetas con medidores**.
- Tablas actuales (Modelos y Grupos): **reemplazadas por completo** (nada de tablas en Recursos).
- Solo pestaña Recursos; Puertos y Procesos quedan como están.

## Tareas

- [x] 1. Reemplazar la tabla de modelos por tarjetas con medidores GTT/RSS/CPU + puerto + PID + archivo del modelo
- [x] 2. Reemplazar la tabla de grupos por barra apilada + leyenda con valores, % de reparto y cantidad de procesos
- [x] 3. Reemplazar el resumen de memoria por barras RAM/SWAP con % real sobre el total
- [x] 4. Agrandar los sparklines del historial a área rellena (28px → 44px)
- [x] 5. Limpiar el CSS muerto (reglas de las tablas eliminadas) y agregar los estilos nuevos
- [x] 6. Verificar build (tsc + vite) y recompilar el binario Tauri con el CLI

## Invariantes que no se tocan

- La API de props de `Recursos` (`snapshot`, `error`, `history`) no cambia.
- Nada de dependencias nuevas: SVG + CSS puros.
- Escalas honestas: RAM/SWAP = porcentaje real del total; GTT/RSS de modelos = relativo al
  máximo del conjunto (se declara en una nota visible); CPU = porcentaje real (cap 100 para la barra).

## Verificación

- `npm run build` (tsc estricto + vite) sin errores.
- Binario Tauri recompilado con el CLI y assets embebidos verificados.
- CSS sin reglas muertas: se eliminaron las de las tablas quitadas (`.model-row`, `.group-row`,
  `.col-model`, `.col-group`, `.memory-summary`, `.mem-row`, `.mem-label`, `.mem-value`).
- `Bar`, `Meter` y `pctOf` se extrajeron a `src/components/Meter.tsx` y la sección/estados a
  `src/components/Section.tsx`, compartidos con la pestaña Tokens.
- Pendiente: confirmación visual del usuario.

## Bloqueo conocido

La revisión nativa de este cambio quedó bloqueada por el fallo de transporte del relay `pi` del
harness (ver `tab-tokens.md`). El código está implementado, compilado e instalado, pero sin
veredicto de revisión.
