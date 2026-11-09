# Real `ps` output, 2026-11-09

Six rows copied verbatim from two `ps` runs on the development machine,
taken seconds apart (T9 of `odd/tasks/macacoview-gtk.md`):

- `ps_comm_after_cpu.txt`: `ps -eo pid=,ppid=,uid=,rss=,%cpu=,comm:64=,args=`,
  the format the 10-05 collector used. The row for PID 475647 shows the bug:
  its RSS (7 digits) and %CPU overflow their columns, so `ps` takes the excess
  out of the `comm` padding and `args` starts two characters early. Cutting
  `comm` at a fixed 64 characters produced the name «pi … p» with args «i».
- `ps_comm_first.txt`: `ps -eo comm:64=,pid=,ppid=,uid=,rss=,%cpu=,args=`,
  the format the collector uses now. Nothing comes before `comm`, so no
  overflow can take its padding.

Only the tail of the Firefox row's `args` (the last column) was cut short.
Every other byte, padding included, is what `ps` printed.
