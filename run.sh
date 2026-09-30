#!/bin/sh
set -eu

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is missing. Install it with Homebrew: brew install python" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Mosh Studio for the first time…"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

if [ -z "${MOSH_PORT:-}" ]; then
  MOSH_PORT=$(.venv/bin/python - <<'PY'
import socket
for port in range(8475, 8500):
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("No free local port found for Mosh Studio")
PY
  )
  export MOSH_PORT
fi

url="http://127.0.0.1:$MOSH_PORT/"
echo "Starting Mosh Studio at $url"
.venv/bin/python server.py &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT INT TERM

ready=0
attempt=0
while [ "$attempt" -lt 40 ]; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    wait "$server_pid" || true
    echo "Mosh Studio could not start. Check the message above." >&2
    exit 1
  fi
  if curl -fs -o /dev/null "$url" 2>/dev/null; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$ready" -ne 1 ]; then
  echo "Mosh Studio did not start in time. Check the message above." >&2
  exit 1
fi

open "$url"
echo "Close this Terminal window or press Control-C to stop Mosh Studio."
wait "$server_pid"
