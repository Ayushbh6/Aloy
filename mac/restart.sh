#!/bin/zsh
# Graceful stop -> rebuild -> reopen. Failure never triggers force quit.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p mac/build
swiftc mac/restart.swift -framework AppKit -o mac/build/restart-helper
mac/build/restart-helper "$PWD/mac/build/Aloy.app" stop
# An older app can exit before its Python child releases the desktop lock.
"$HOME/Library/Application Support/Aloy/runtime/venv/bin/python" -c '
import fcntl, pathlib, time
path = pathlib.Path.home() / "Library/Application Support/Aloy/desktop.lock"
with path.open("a") as lock:
    for attempt in range(100):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            time.sleep(0.1)
    else:
        raise SystemExit("Backend still shutting down; restart aborted without force quit.")
'
zsh mac/build.sh
open "$PWD/mac/build/Aloy.app"
echo "Aloy rebuilt and reopened."
