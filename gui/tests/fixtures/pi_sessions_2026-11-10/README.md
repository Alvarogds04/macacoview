# Real Pi process trees, 2026-11-10

`capture.json` is a read-only capture of the development machine taken
2026-11-10 12:34 CET (T14 of `odd/tasks/macacoview-gtk.md`), while 11 `pi`
processes were running: 6 sessions and 5 subagents (a `pi` whose ancestor is
another `pi`). It rebuilds a fake `/proc` and `~/.pi/agent` in
`tests/test_pi_sesiones.py`.

Kept, per process in a pi tree (the pi plus every descendant via ppid) and
their ancestors: pid, ppid, uid, comm, ps RSS and %CPU; for tree members also
the `exe` link, `starttime` (stat field 22), `Pss`/`Rss` from `smaps_rollup`
(`null` where it was unreadable: 5 processes) and, for `pi`, the `cwd` link.

Trimmed for privacy: the home directory is replaced by `@HOME@`; no command
lines (`args` is rebuilt as the comm); from `environ` only `PI_SESSION_ID` and
`PI_SESSION_FILE` (present in two descendants of subagent 3101730); from each
candidate session file only the header fields `type`, `version`, `id`,
`timestamp`, `cwd`, its size and mtime, and the `session_info` entries (none of
these sessions was named). No message, prompt or tool output.

Facts the tests rely on: 3102734 and 3102737 started in the same clock tick
with the same cwd and two session files created in the same second, so start
time alone cannot tell them apart; 5544 and 5890 started on 11-04 (resumed
sessions, no file created at start); 2922080 had no session file yet.
