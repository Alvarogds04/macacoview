#!/bin/sh
# Fixture for the token tests: the document a script-based source returns.
#
# It lives in git, executable, on purpose. The tests used to write an
# executable script into a temporary directory and exec it immediately, and a
# fork() in another test thread during that write inherited the open-for-write
# descriptor, which keeps the inode "open for writing" for as long as the child
# lives: exec'ing that same inode then fails with ETXTBSY (os error 26) on
# roughly one full-suite run in ten. A file that is never written at test time
# cannot lose that race.
echo '{"remote":{"status":"ok","total":77},"local":{"status":"ok","total":3},"pi":{"status":"ok","total":9},"codex_cli":{"status":"ok","total":1},"claude_code":{"status":"ok","total":2},"codex":{"status":"ok","plan":"prolite"}}'
