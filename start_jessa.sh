#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${JESSA_HOST:-0.0.0.0}"
PORT="${JESSA_PORT:-8765}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it with:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  python -m pip install -r requirements.txt"
  exit 1
fi

mkdir -p data

if .venv/bin/python - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sys.exit(0)
    sys.exit(1)
PY
then
  echo "Port $PORT is already in use. If JESSA is already running, open:"
  echo "  http://127.0.0.1:$PORT"
  echo
  echo "To use another port:"
  echo "  JESSA_PORT=8766 ./start_jessa.sh"
  exit 1
fi

echo "Initializing PostgreSQL database..."
.venv/bin/python -c 'from jessa_app.db import init_db; init_db()'

echo "Starting JESSA on $HOST:$PORT"
echo "Local access: http://127.0.0.1:$PORT"
echo "LAN access: http://<this-mac-10.0.x.x-ip>:$PORT"
exec .venv/bin/uvicorn jessa_app.main:app --host "$HOST" --port "$PORT"
