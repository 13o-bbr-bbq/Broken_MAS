FROM python:3.12-slim
WORKDIR /app

# dashboard の依存パッケージをインストール
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

# evaluation_client / threat_modeling_agent / visualization（dashboard から sys.path 参照）
COPY evaluation_client/ evaluation_client/
COPY threat_modeling_agent/ threat_modeling_agent/
COPY visualization/ visualization/

# dashboard 本体
COPY dashboard/ dashboard/

WORKDIR /app/dashboard

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.enableXsrfProtection=false"]
