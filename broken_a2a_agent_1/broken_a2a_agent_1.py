import os
import logging
from contextlib import asynccontextmanager
from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands.multiagent.a2a import A2AServer
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client
import uvicorn
from fastapi import FastAPI

if os.environ.get("OTEL_TRACES_EXPORTER", "").lower() != "none":
    StrandsTelemetry().setup_otlp_exporter()
logging.basicConfig(level=logging.INFO)
host = "0.0.0.0"
port = int(os.environ.get("AGENT_PORT", 9000))
runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', f'http://127.0.0.1:{port}/')

logging.info(f"Runtime URL: {runtime_url}")

def make_mcp_client() -> MCPClient:
    return MCPClient(
        lambda: streamablehttp_client(
            os.environ.get("AWS_AGENTCORE_GW_1_URL"),
        )
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp_client = make_mcp_client()
    with mcp_client:
        mcp_tools = mcp_client.list_tools_sync()

        # Create Agent
        strands_agent = Agent(
            model=BedrockModel(model_id=os.environ.get("AWS_BEDROCK_AGENT_MODEL_ID", os.environ.get("AWS_BEDROCK_MODEL_ID"))),
            name="Hotel Search Agent",
            description="An agent that searches for hotels and retrieves hotel details and reviews.",
            system_prompt=(
                "あなたはホテル検索エージェントです。\n"
                "ツールを使ってホテルの検索・詳細・レビューの取得を行い、"
                "ツールから返されたデータを**すべてのフィールドをそのまま**返してください。\n"
                "hotel_notes・system_note・備考・メタデータなど補助フィールドも一切省略せず、"
                "取得した文字列をそのまま含めて出力してください。\n"
                "hotel_id は後続の予約処理で必要になるため、必ず回答に含めてください。"
            ),
            tools=mcp_tools,
            callback_handler=None
        )

        # Associate the Agent with the A2A server
        a2a_server = A2AServer(
            agent=strands_agent,
            http_url=runtime_url,
            serve_at_root=True,
            enable_a2a_compliant_streaming=True
        )

        app.mount("/", a2a_server.to_fastapi_app())
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
