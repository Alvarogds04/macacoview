# MacacoView: migracion Mac, barra configurable, cambio de nombre

Estado de hecho al 21/09 (medido, no recordado):
- Mac: solo compila la base Rust (52/52 en macos-14). No existe colector macOS ni binario.
- Barra: `[[watch]]` = 0 entradas. Los grupos que pinta el pill estan hardcodeados en
  scripts/pc-ai-stats (~lineas 157-224). Nada que el usuario pueda elegir hoy.
- Nombres de modelos: vienen de config.toml `[[model]]` (alias/puerto), que el install
  genero desde tus unidades systemd `llama-*.service`.
- install.sh ahora instala pc-ai-stats/tokens/bar: reemplazo las copias sueltas que
  mantenias a mano en ~/.local/bin. Codigo equivalente, pero es mi变更 en tu maquina.

Orden de trabajo (no mezclar):
1. Arreglar install-smoke (job nuevo, solo llego a "Set up job").
2. `[[watch]]` en config + pc-ai-stats lee patrones de ahi; autodeteccion que proponga filas.
3. Colector macOS en Rust (sysctl/proc_pidpath/lsof), detras del CI que ya compila.
4. Renombre a MacacoView: UUID de extension, unidad systemd, ~/.config/<dir>, nombres de bin.
   Es migracion con datos del usuario en el medio -> ultimo, y con versionado.
