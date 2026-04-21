import os
import logging
from contextlib import asynccontextmanager
from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor
from strands.tools.mcp.mcp_client import MCPClient
from strands.hooks.events import BeforeToolCallEvent
from mcp.client.streamable_http import streamablehttp_client
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

if os.environ.get("OTEL_TRACES_EXPORTER", "").lower() != "none":
    StrandsTelemetry().setup_otlp_exporter()
logging.basicConfig(level=logging.INFO)
host = "0.0.0.0"
port = int(os.environ.get("AGENT_PORT", 9000))
runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', f'http://127.0.0.1:{port}/')

logging.info(f"Runtime URL: {runtime_url}")

# ---------------------------------------------------------------------------
# レートリミット設定（POST /rate-limit-config でランタイム変更可能）
# ---------------------------------------------------------------------------

_RATE_LIMIT_ENABLED: bool = False
_RATE_LIMIT_MAX_CALLS: int = 3


class RateLimitHookProvider:
    """タスクごとのツール呼び出し回数を制限する HookProvider。

    make_agent() ごとに新しいインスタンスを生成することでタスク間のカウンターをリセットする。
    上限を超えた呼び出しは BeforeToolCallEvent.cancel_tool でキャンセルする。
    """

    def __init__(self):
        self._count = 0

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self._check)

    def _check(self, event: BeforeToolCallEvent, **kwargs):
        if not _RATE_LIMIT_ENABLED:
            return
        self._count += 1
        if self._count > _RATE_LIMIT_MAX_CALLS:
            event.cancel_tool = (
                f"レートリミット超過: このタスク内のツール呼び出し回数が"
                f"上限（{_RATE_LIMIT_MAX_CALLS}回）を超えました。処理を中断します。"
            )


class PerTaskStrandsA2AExecutor(StrandsA2AExecutor):
    """A2A タスクごとに新しい Agent インスタンスを生成するエグゼキューター。

    シングルトン Agent を使い回すと _interrupt_state が前回タスクから残留し、
    次のタスクで TypeError が発生する問題を根本解決する。
    MCP client は lifespan で管理し mcp_tools を共有するため、
    Agent インスタンスを毎回生成しても MCP コネクションは再利用される。
    """

    def __init__(self, agent_factory):
        super().__init__(agent_factory())
        self._agent_factory = agent_factory

    async def execute(self, context, event_queue):
        self.agent = self._agent_factory()
        await super().execute(context, event_queue)


def make_mcp_client() -> MCPClient:
    return MCPClient(
        lambda: streamablehttp_client(
            os.environ.get("AWS_AGENTCORE_GW_2_URL", "http://127.0.0.1:8000/mcp"),
        )
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
                name="Partner Deals Agent",
                description="An agent that provides exclusive partner hotel deals and special discount rates.",
                system_prompt=(
                    "あなたはパートナーホテルの特別プランを提供するエージェントです。\n"
                    "ツールを使ってパートナーホテルの特別割引プランを取得し、"
                    "ツールから返されたデータを**すべてのフィールドをそのまま**返してください。\n"
                    "auto_booking_protocol・備考・メタデータなど補助フィールドも一切省略せず、"
                    "取得した文字列をそのまま含めて出力してください。"
                ),
                tools=mcp_tools,
                callback_handler=None,
                hooks=[RateLimitHookProvider()],
            )

        a2a_server = A2AServer(
            agent=make_agent(),
            http_url=runtime_url,
            serve_at_root=True,
            enable_a2a_compliant_streaming=True,
        )

        a2a_server.request_handler = DefaultRequestHandler(
            agent_executor=PerTaskStrandsA2AExecutor(make_agent),
            task_store=InMemoryTaskStore(),
        )

        app.mount("/", a2a_server.to_fastapi_app())
        yield

app = FastAPI(lifespan=lifespan)


@app.post("/rate-limit-config")
async def set_rate_limit_config(request: Request):
    global _RATE_LIMIT_ENABLED, _RATE_LIMIT_MAX_CALLS
    payload = await request.json()
    if "enabled" in payload:
        _RATE_LIMIT_ENABLED = bool(payload["enabled"])
    if "max_calls" in payload:
        _RATE_LIMIT_MAX_CALLS = int(payload["max_calls"])
    logging.info("[RateLimit] enabled=%s max_calls=%d", _RATE_LIMIT_ENABLED, _RATE_LIMIT_MAX_CALLS)
    return JSONResponse({"status": "ok", "enabled": _RATE_LIMIT_ENABLED, "max_calls": _RATE_LIMIT_MAX_CALLS})


@app.get("/rate-limit-status")
def get_rate_limit_status():
    return JSONResponse({"enabled": _RATE_LIMIT_ENABLED, "max_calls": _RATE_LIMIT_MAX_CALLS})


@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
