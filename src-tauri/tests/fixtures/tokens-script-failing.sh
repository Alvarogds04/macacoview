#!/bin/sh
# Fixture for the token tests: a script that runs and exits non-zero. That must
# stay an error — only a missing binary degrades the document. Committed and
# executable for the same reason as `tokens-script-ok.sh`: writing an
# executable at test time races with fork() in other test threads (ETXTBSY).
echo boom >&2
exit 3
