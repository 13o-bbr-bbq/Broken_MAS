#!/bin/bash
# ============================================================
# ローカル検証環境 一括起動スクリプト
#
# 使い方:
#   ./start_local.sh          # 全コンポーネント起動
#   ./start_local.sh --no-rogue  # Rogue Agent なしで起動（正常系のみ）
#
# 停止:
#   ./stop_local.sh
#
# ログ確認:
#   tail -f logs/<component>.log
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.local_pids"
LOG_DIR="$SCRIPT_DIR/logs"
WITH_ROGUE=true

# オプション解析
for arg in "$@"; do
  case $arg in
    --no-rogue) WITH_ROGUE=false ;;
  esac
done

# ── 二重起動チェック ──────────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
  echo "[ERROR] 既に起動中のようです。先に ./stop_local.sh を実行してください。"
  exit 1
fi

# ── 環境変数の読み込み ────────────────────────────────────────────────────
source "$SCRIPT_DIR/export_env_vars_local.sh"

# Rogue なしの場合は URL を未設定にしてオーケストレーターのツールリストから除外
if [ "$WITH_ROGUE" = false ]; then
  unset AWS_A2A_SERVER_RUNTIME_3_URL
  echo "[INFO] Rogue Agent なしモードで起動します。"
fi

mkdir -p "$LOG_DIR"
> "$PID_FILE"  # PID ファイルを初期化

# ── 使用ポートのクリーンアップ ───────────────────────────────────────────
echo "[CLEAN] 既存の MAS プロセスを終了します ..."
for script in local_mcp_gateway_1.py local_mcp_gateway_2.py \
              broken_a2a_agent_1.py broken_a2a_agent_2.py \
              rogue_a2a_agent_1.py broken_a2a_orchestrator_agent_1.py; do
  if pkill -f "$script" 2>/dev/null; then
    echo "        $script: 終了しました"
  fi
done
sleep 2  # ポート解放を待つ

# ── ヘルパー関数 ──────────────────────────────────────────────────────────

start_process() {
  local name="$1"
  local log="$LOG_DIR/${name}.log"
  shift
  echo "[START] $name ..."
  "$@" > "$log" 2>&1 &
  local pid=$!
  echo "$pid $name" >> "$PID_FILE"
  echo "        PID=$pid  log=$log"
}

wait_for_port() {
  local port="$1"
  local name="$2"
  local max=60   # 30秒待つ（0.5s × 60）
  local count=0
  while ! (echo > /dev/tcp/localhost/"$port") 2>/dev/null; do
    count=$((count + 1))
    if [ $count -ge $max ]; then
      echo "[WARN]  $name (port $port) がタイムアウトしました。ログを確認してください。"
      echo "        cat logs/${name}.log"
      return 0  # 警告のみ。スクリプトは続行する
    fi
    sleep 0.5
  done
  echo "        $name (port $port) 起動確認 ✓"
}

# ── Step 1: ローカル MCP ゲートウェイ ────────────────────────────────────
echo ""
echo "=== Step 1: Local MCP Gateways ==="

start_process "local_mcp_gateway_1" \
  python "$SCRIPT_DIR/local_mcp_gateway_1.py"

start_process "local_mcp_gateway_2" \
  python "$SCRIPT_DIR/local_mcp_gateway_2.py"

wait_for_port 8010 "local_mcp_gateway_1"
wait_for_port 8020 "local_mcp_gateway_2"

# ── Step 2: A2A エージェント ─────────────────────────────────────────────
echo ""
echo "=== Step 2: A2A Agents ==="

start_process "broken_a2a_agent_1" \
  env AGENT_PORT=9011 AGENTCORE_RUNTIME_URL=http://localhost:9011/ \
    python "$SCRIPT_DIR/broken_a2a_agent_1/broken_a2a_agent_1.py"

start_process "broken_a2a_agent_2" \
  env AGENT_PORT=9012 AGENTCORE_RUNTIME_URL=http://localhost:9012/ \
    python "$SCRIPT_DIR/broken_a2a_agent_2/broken_a2a_agent_2.py"

if [ "$WITH_ROGUE" = true ]; then
  start_process "rogue_a2a_agent_1" \
    env AGENT_PORT=9003 AGENTCORE_RUNTIME_URL=http://localhost:9003/ \
      python "$SCRIPT_DIR/rogue_a2a_agent_1/rogue_a2a_agent_1.py"
fi

wait_for_port 9011 "broken_a2a_agent_1"
wait_for_port 9012 "broken_a2a_agent_2"
if [ "$WITH_ROGUE" = true ]; then
  wait_for_port 9003 "rogue_a2a_agent_1"
fi

# ── Step 3: オーケストレーター ───────────────────────────────────────────
echo ""
echo "=== Step 3: Orchestrator ==="

start_process "orchestrator" \
  python "$SCRIPT_DIR/broken_a2a_orchestrator_1/broken_a2a_orchestrator_agent_1.py"

wait_for_port 8080 "orchestrator"

# ── 起動完了 ─────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  全コンポーネント起動完了"
echo "============================================"
echo "  MCP Gateway 1 : http://localhost:8010/mcp"
echo "  MCP Gateway 2 : http://localhost:8020/mcp"
echo "  A2A Agent 1   : http://localhost:9011/"
echo "  A2A Agent 2   : http://localhost:9012/"
if [ "$WITH_ROGUE" = true ]; then
echo "  Rogue Agent   : http://localhost:9003/"
fi
echo "  Orchestrator  : http://localhost:8080/"
echo "--------------------------------------------"
echo "  Dashboard を起動する場合:"
echo "    streamlit run dashboard/app.py"
echo "  Chat ページで URL: http://localhost:8080 を入力"
echo "--------------------------------------------"
echo "  停止: ./stop_local.sh"
echo "  ログ: tail -f logs/<component>.log"
echo "============================================"
