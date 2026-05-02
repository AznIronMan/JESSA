#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="${JESSA_PORT:-8765}"
PIDS=()

add_pid() {
  local pid="$1"
  local existing
  [[ -n "$pid" ]] || return 0
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  existing=" ${PIDS[*]-} "
  if [[ "$existing" != *" $pid "* ]]; then
    PIDS+=("$pid")
  fi
}

is_jessa_pid() {
  local pid="$1"
  local command
  local cwd
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *"jessa_app.main:app"* ]] || return 1
  [[ "$command" == *"$ROOT_DIR"* ]] && return 0
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  [[ "$cwd" == "$ROOT_DIR" ]]
}

if [[ -f data/server.pid ]]; then
  pid="$(tr -dc '0-9' < data/server.pid || true)"
  if [[ -n "$pid" ]] && is_jessa_pid "$pid"; then
    add_pid "$pid"
  fi
fi

if command -v pgrep >/dev/null 2>&1; then
  while IFS= read -r pid; do
    if is_jessa_pid "$pid"; then
      add_pid "$pid"
    fi
  done < <(pgrep -f "jessa_app.main:app" 2>/dev/null || true)
fi

if command -v lsof >/dev/null 2>&1; then
  while IFS= read -r pid; do
    if is_jessa_pid "$pid"; then
      add_pid "$pid"
    fi
  done < <(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
fi

if [[ "${#PIDS[@]}" -eq 0 ]]; then
  echo "No running JESSA web app process found."
  rm -f data/server.pid
  exit 0
fi

echo "Stopping JESSA PID(s): ${PIDS[*]}"
kill -TERM "${PIDS[@]}" 2>/dev/null || true

for _ in {1..20}; do
  running=()
  for pid in "${PIDS[@]}"; do
    if ps -p "$pid" >/dev/null 2>&1; then
      running+=("$pid")
    fi
  done
  if [[ "${#running[@]}" -eq 0 ]]; then
    rm -f data/server.pid
    echo "JESSA stopped."
    exit 0
  fi
  sleep 0.5
done

echo "Force-stopping JESSA PID(s): ${running[*]}"
kill -KILL "${running[@]}" 2>/dev/null || true
rm -f data/server.pid
echo "JESSA stopped."
