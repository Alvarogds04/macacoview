# macOS collector: plan

## Captured on real hardware (supersedes the guesses below)

`src-tauri/tests/fixtures/macos-capture.txt` is authentic output from the
`macos-14` runner, produced by `.github/workflows/macos-capture.yml`. Parsers must
match that file, not the assumptions in this document. It is a VM, so hardware
shaped things reflect paravirtualised devices.

What the capture corrected:

1. **Page size is 16384 on Apple Silicon, not 4096.** Any fixture that hardcodes
   4096 is wrong for this platform. The parser must take it from `hw.pagesize`.
2. **`vm_stat` has no `Page size of bytes` line.** The real header is
   `Mach Virtual Memory Statistics: (page size of 16384 bytes)`, several lines
   above the counters. The branch in `parse_vm_stat` looking for that key never
   fires on real output; it silently falls back to `hw.pagesize`, which happens to
   be correct. Do not "fix" it by parsing the header without checking both.
3. **`ps` truncates `comm` to 16 characters as soon as `args` is requested.**
   `ps -ww -axo pid=,comm=` prints the full path; adding `args=` to the same
   invocation cuts `comm` to `/System/Library/`. So a parser that reads `comm` as
   "the trailing fields" gets a truncated name glued to a full command line. Use
   two separate `ps` calls, or `args` alone and derive the name from it.
4. **`ioreg -r -d 1 -w 0 -c IOAccelerator` does NOT carry
   `PerformanceStatistics`.** That node exposes `AGCInfo`, `MetalPluginName` and
   similar. The statistics live on another node and only show up with
   `ioreg -l -w 0` (grep it). Keys seen there: `"Alloc system memory"` and
   `"In use system memory"`, in bytes.
5. **`system_profiler SPDisplaysDataType` printed nothing** on the runner, so it
   is not a usable fallback.
6. **`lsof -nP -iTCP -sTCP:LISTEN` is confirmed compatible with `lsof_to_ss`.**
   Real columns are `COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME`, so the
   address is the 9th field; the existing parser's `nth(6)` after consuming
   `command` and `pid` lands exactly there. No change needed.
7. **No model server exists on a clean runner** (`ollama`, `llama-server`,
   `vllm` all absent), so Ollama-shaped detection cannot be fixture-verified in
   CI as things stand. Either capture on a machine that has it, or install it in
   the capture job -- do not invent its output.

Because the runner is a VM, the GPU picture is `AppleParavirtGPU`. Silicon in a
retail machine reports `AGXAccelerator` and may expose more keys than are visible
here; that must be confirmed on a real Mac before the GPU field is trusted.


Analysis session, read-only. Nothing was edited to produce this; the findings below
are what a writer should implement.

## Corrections to what was assumed before

- `[[model]]` does not exist in this tree: `config.py` does not define it and the
  Rust side has no TOML dependency. A model alias must be derived from the provider,
  not read from config.
- `codexbar` can be deleted. Codex `rate_limits` already arrive inside its own
  rollout `.jsonl`, so shelling out to a separate binary is unnecessary.
- The 11 macOS tests do not run on Linux because the whole `macos` module sits behind
  `cfg`. Linux reports 52 tests, macOS 63. Moving the pure parsers out of the gate
  raises the Linux gate to 63.

## 1. Model detection without /proc

Use `ps -ww -axo pid=,ppid=,uid=,rss=,%cpu=,comm=,args=` joined with
`lsof -nP -iTCP -sTCP:LISTEN` by pid (that path already exists: `lsof_to_ss` +
`parse_ss_output`, 40 tests).

**`-ww` is mandatory.** Without it macOS truncates `args` and the `--alias`/`--port`
flags are invisible: a silent failure, not an error.

Model identity is not uniform per runtime:

| Runtime | Where the model name comes from |
| --- | --- |
| llama.cpp | argv `--alias` / `--model` / `--port`, or `GET /props` |
| Ollama | **not** in argv: it runs in an `ollama runner` child, so `ollama ps` or `GET /api/ps` |
| vLLM / MLX | argv `vllm serve <model>`, or `GET /v1/models` |

Memory: `rss` underestimates Metal on Apple Silicon. The honest figure is
`phys_footprint` via `proc_pid_rusage(RUSAGE_INFO_V4)`.

Performance: `lsof` can take seconds, so it needs its own cadence
(`MODELS_INTERVAL_SECS`, ~5s) with a cache. Not at 1 Hz.

## 2. Unified memory: do not invent a GPU number

There is no dedicated VRAM and **no public per-process GPU memory API** on macOS.
`MTLDevice.currentAllocatedSize` only sees the calling process (~0 for a monitor),
and `IORegistry`/`IOAccelerator` `PerformanceStatistics` is per device and
system-wide (the same source Activity Monitor's GPU graph uses).

Therefore a per-model `gtt_gib` on Darwin is impossible without fabricating it:

- `gtt_gib` / `vram_gib` become `Option<f64>` → `null` on Darwin; the UI shows "—"
  or "unified", never `0.0G`.
- Add a machine-level `Stats.gpu`. NOT from `ioreg -r -d 1 -w 0 -c IOAccelerator`:
  the capture shows that node carries no `PerformanceStatistics`. Use
  `ioreg -l -w 0` and grep for it (see the captured findings at the top).
- `phys_footprint` covers per-process memory.

Rejected: putting `wired` from `vm_stat` into GTT, or copying RSS as GTT.

## 3. Testable on Linux vs needs a real Mac

Rule: pure parsers in a module **without** `cfg`; only the subprocess boundary gets
`#[cfg(target_os = "macos")]`.

Testable from captured text: `vm_stat`, `sysctl`, `vm.swapusage` (already), `ps -ww`
args, `lsof`→`ss` (already), `ioreg` PerformanceStatistics, `ollama ps`/`/api/ps`,
`/v1/models`, `/metrics`, `/props`, and the Codex/Claude transcripts (100%
cross-platform, they are files).

Needs real hardware: capturing and confirming `ioreg` key names per chip (M1..M4),
`lsof`/TCC permissions, and `phys_footprint` vs `rss` under real Metal load.

## 4. Risks and order

Top risks: truncated `args` without `-ww` (silent); **double counting tokens**
(Codex `total_token_usage` is cumulative per session, so take the last value per
file rather than summing lines; Claude emits several assistant entries per turn, so
dedupe); `lsof` at 1 Hz; the Ollama model name living in a child; sandbox/TCC if the
app is ever notarised.

Order:

- **F0** decisions: `Option` vs `0.0`; token parsers in Rust vs the Python script;
  Tauri vs `gui/` GTK scope.
- **F1** testability refactor (pure parsers out of the `cfg` gate).
- **F2** fixtures + `ioreg` parser.
- **F3** model detection + groups.
- **F4** tokens: Codex / Claude / llama.cpp.
- **F5** CI Linux at 63 + `macos-14` smoke + a runbook for a real Mac.
