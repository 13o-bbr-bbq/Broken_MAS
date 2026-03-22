FROM python:3.12-slim
WORKDIR /app

# dashboard の依存パッケージをインストール
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

# evaluation_client / threat_modeling_agent / visualization（dashboard から sys.path 参照）
COPY evaluation_client/ evaluation_client/
COPY threat_modeling_agent/ threat_modeling_agent/
COPY visualization/ visualization/

# Docker Compose ローカルトポロジー生成に必要なファイル群
# （build_graph_from_compose が /app 配下を参照する）
COPY docker-compose.yml docker-compose.yml
COPY Dockerfile.mcp-gateway-1 Dockerfile.mcp-gateway-1
COPY Dockerfile.mcp-gateway-2 Dockerfile.mcp-gateway-2
COPY broken_mcp_server_1/ broken_mcp_server_1/
COPY broken_mcp_server_2/ broken_mcp_server_2/
COPY broken_mcp_server_3/ broken_mcp_server_3/
COPY broken_mcp_server_4/ broken_mcp_server_4/
COPY rogue_mcp_server_1/ rogue_mcp_server_1/

# dashboard 本体
COPY dashboard/ dashboard/

WORKDIR /app/dashboard

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.enableXsrfProtection=false"]
