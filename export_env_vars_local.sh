#!/bin/bash
# ============================================================
# ローカル検証用 環境変数設定スクリプト
#
# 使い方:
#   source ./export_env_vars_local.sh
#
# 起動順序（別ターミナルで各コマンドを実行）:
#   1. python local_mcp_gateway_1.py              # port 8010
#   2. python local_mcp_gateway_2.py              # port 8020
#   3. AGENT_PORT=9011 AGENTCORE_RUNTIME_URL=http://localhost:9011/ \
#        python broken_a2a_agent_1/broken_a2a_agent_1.py
#   4. AGENT_PORT=9012 AGENTCORE_RUNTIME_URL=http://localhost:9012/ \
#        python broken_a2a_agent_2/broken_a2a_agent_2.py
#   5. AGENT_PORT=9003 AGENTCORE_RUNTIME_URL=http://localhost:9003/ \
#        python rogue_a2a_agent_1/rogue_a2a_agent_1.py
#   6. python broken_a2a_orchestrator_1/broken_a2a_orchestrator_agent_1.py
#        # → http://localhost:8080/invocations
#   7. streamlit run dashboard/app.py
#        # → Chat ページで URL: http://localhost:8080 を入力
# ============================================================

# ── Amazon Bedrock ──────────────────────────────────────────────────────────
# Orchestrator / Steering Judge: 高精度モデル（守る側）
export AWS_BEDROCK_MODEL_ID="anthropic.claude-3-5-sonnet-20240620-v1:0"
# A2A Agent 1/2 本体 / Rogue Agent: 軽量モデル（攻撃を受ける側・インジェクション検証用）
export AWS_BEDROCK_AGENT_MODEL_ID="anthropic.claude-3-5-haiku-20241022-v1:0"

# ── A2A エージェント URL（ローカルポートを指定）──────────────────────────
export AWS_A2A_SERVER_RUNTIME_1_URL="http://localhost:9011/"
export AWS_A2A_SERVER_RUNTIME_2_URL="http://localhost:9012/"
export AWS_A2A_SERVER_RUNTIME_3_URL="http://localhost:9003/"

# ── MCP ゲートウェイ URL（ローカル集約ゲートウェイを指定）──────────────
export AWS_AGENTCORE_GW_1_URL="http://localhost:8010/mcp"
export AWS_AGENTCORE_GW_2_URL="http://localhost:8020/mcp"

# ── OTEL（ローカルでは送信無効）────────────────────────────────────────
export OTEL_TRACES_EXPORTER="none"
export OTEL_SERVICE_NAME="broken_mas_local"
unset OTEL_EXPORTER_OTLP_HEADERS   # 本番用ヘッダーが残っていると OTLP 401 になるため削除

# ── AgentOps ログファイル無効────────────────────────────────────────────
export AGENTOPS_LOGGING_TO_FILE="false"

# ── AWS 認証情報（Bedrock Guardrail を使う場合のみ必要）────────────────
# export AWS_ACCESS_KEY_ID=""
# export AWS_SECRET_ACCESS_KEY=""
# export AWS_SESSION_TOKEN=""
export AWS_DEFAULT_REGION="us-west-2"

# ── Bedrock Guardrail（Dashboard Chat ページ用・任意）──────────────────
export BEDROCK_GUARDRAIL_ID=""
export BEDROCK_GUARDRAIL_VERSION=""

# ── Langfuse（Dashboard の Evaluation / Visualization ページ用・任意）──
export LANGFUSE_SECRET_KEY=""
export LANGFUSE_PUBLIC_KEY=""
export LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"

# ── 確認表示────────────────────────────────────────────────────────────
echo "=== ローカル環境変数を設定しました ==="
echo "AWS_A2A_SERVER_RUNTIME_1_URL : $AWS_A2A_SERVER_RUNTIME_1_URL"
echo "AWS_A2A_SERVER_RUNTIME_2_URL : $AWS_A2A_SERVER_RUNTIME_2_URL"
echo "AWS_A2A_SERVER_RUNTIME_3_URL : $AWS_A2A_SERVER_RUNTIME_3_URL"
echo "AWS_AGENTCORE_GW_1_URL       : $AWS_AGENTCORE_GW_1_URL"
echo "AWS_AGENTCORE_GW_2_URL       : $AWS_AGENTCORE_GW_2_URL"
echo "AWS_BEDROCK_MODEL_ID         : $AWS_BEDROCK_MODEL_ID"
echo "AWS_BEDROCK_AGENT_MODEL_ID   : $AWS_BEDROCK_AGENT_MODEL_ID"
echo "OTEL_TRACES_EXPORTER         : $OTEL_TRACES_EXPORTER"
echo ""
echo "起動手順は本ファイルのコメントを参照してください。"
