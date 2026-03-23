import os
import logging
from contextlib import asynccontextmanager
from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
import uvicorn
from fastapi import FastAPI

if os.environ.get("OTEL_TRACES_EXPORTER", "").lower() != "none":
    StrandsTelemetry().setup_otlp_exporter()
logging.basicConfig(level=logging.INFO)
host = "0.0.0.0"
port = int(os.environ.get("AGENT_PORT", 9000))
runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', f'http://127.0.0.1:{port}/')

logging.info(f"Runtime URL: {runtime_url}")


class PerTaskStrandsA2AExecutor(StrandsA2AExecutor):
    """A2A タスクごとに新しい Agent インスタンスを生成するエグゼキューター。

    シングルトン Agent を使い回すと _interrupt_state が前回タスクから残留し、
    次のタスクで「must resume from interrupt with list of interruptResponse's」
    TypeError が発生する。タスクごとに新しい Agent を生成することで
    _interrupt_state・messages を常にクリーンな状態に保つ。

    MCP client は lifespan で管理し mcp_tools を共有するため、
    Agent インスタンスを毎回生成しても MCP コネクションは再利用される。
    """

    def __init__(self, agent_factory):
        # 親クラスの __init__ は agent を必要とするため初回インスタンスで初期化する
        super().__init__(agent_factory())
        self._agent_factory = agent_factory

    async def execute(self, context, event_queue):
        # タスクごとに新しい Agent を生成して割り当てる
        self.agent = self._agent_factory()
        await super().execute(context, event_queue)


def make_mcp_client() -> MCPClient:
    return MCPClient(
        lambda: streamablehttp_client(
            os.environ.get("AWS_AGENTCORE_GW_2_URL"),
        )
    )

_AGENT_SYSTEM_PROMPT = (
    "あなたはホテル予約エージェントです。\n"
    "以下の手順でホテルを予約してください:\n"
    "1. check_availability ツールを呼び出して空室状況と料金を確認する。\n"
    "2. ユーザーが希望する room_type を特定する（未指定の場合は standard を選択）。\n"
    "3. make_reservation ツールを呼び出して予約を確定する。\n"
    "予約完了後、予約番号・ホテル名・部屋タイプ・宿泊日程・合計金額を日本語で返してください。"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp_client = make_mcp_client()
    with mcp_client:
        mcp_tools = mcp_client.list_tools_sync()

        def make_agent() -> Agent:
            """タスクごとに呼ばれる Agent ファクトリー。mcp_tools は lifespan スコープで共有する。"""
            return Agent(
                model=BedrockModel(model_id=os.environ.get("AWS_BEDROCK_AGENT_MODEL_ID", os.environ.get("AWS_BEDROCK_MODEL_ID"))),
                name="Hotel Booking Agent",
                description="An agent that checks hotel room availability and makes reservations.",
                system_prompt=_AGENT_SYSTEM_PROMPT,
                tools=mcp_tools,
                callback_handler=None,
            )

        # A2AServer はメタデータ（AgentCard）生成目的で作成する
        a2a_server = A2AServer(
            agent=make_agent(),
            http_url=runtime_url,
            serve_at_root=True,
            enable_a2a_compliant_streaming=True,
        )

        # タスクごとに新しい Agent を生成するエグゼキューターに差し替える
        # これにより _interrupt_state・messages のタスク間残留を防ぐ
        a2a_server.request_handler = DefaultRequestHandler(
            agent_executor=PerTaskStrandsA2AExecutor(make_agent),
            task_store=InMemoryTaskStore(),
        )

        app.mount("/", a2a_server.to_fastapi_app())
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
