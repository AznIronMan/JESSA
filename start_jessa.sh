#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it with:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  python -m pip install -r requirements.txt"
  exit 1
fi

dotenv_value() {
  local name="$1"
  .venv/bin/python - "$name" <<'PY'
from pathlib import Path
import sys

try:
    from dotenv import dotenv_values
except Exception:
    sys.exit(0)

value = dotenv_values(Path.cwd() / ".env").get(sys.argv[1])
if value:
    print(value)
PY
}

HOST="${JESSA_HOST:-$(dotenv_value JESSA_HOST)}"
HOST="${HOST:-0.0.0.0}"
PORT="${JESSA_PORT:-$(dotenv_value JESSA_PORT)}"
PORT="${PORT:-9122}"

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Invalid JESSA_PORT '$PORT'. Set JESSA_PORT to a numeric TCP port from 1 to 65535."
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

echo "Starting JESSA on $HOST:$PORT"
echo "Startup checks will report missing or invalid .env settings in the web UI."
echo "Local access: http://127.0.0.1:$PORT"
echo "LAN access: http://<this-mac-10.0.x.x-ip>:$PORT"
exec .venv/bin/uvicorn jessa_app.main:app --host "$HOST" --port "$PORT"
