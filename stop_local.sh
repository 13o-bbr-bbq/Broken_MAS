#!/bin/bash
# ============================================================
# ローカル検証環境 一括停止スクリプト
#
# 使い方:
#   ./stop_local.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.local_pids"

if [ ! -f "$PID_FILE" ]; then
  echo "[INFO] 起動中のプロセスが見つかりません（$PID_FILE が存在しない）。"
  exit 0
fi

echo "=== ローカル検証環境を停止します ==="

while read -r pid name; do
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "[STOP] $name (PID=$pid)"
  else
    echo "[SKIP] $name (PID=$pid) は既に停止済みです。"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
echo ""
echo "全プロセスを停止しました。"
