import os
import logging
import time
import threading
from contextlib import asynccontextmanager
from strands import Agent
from llm_factory import make_model
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
_RATE_LIMIT_WINDOW_SEC: float = 60.0

# プロセス全体で共有するツール呼び出しのタイムスタンプ列（スライディングウィンドウ）。
# タスク／セッション（contextId）を横断して集計するため、Orchestrator がリクエストを
# 複数タスクに分割しても累積でカウントされ、タスク単位のカウンタリセットでは回避できない。
_rate_lock = threading.Lock()
_rate_call_times: list[float] = []


class RateLimitHookProvider:
    """プロセス横断＋時間窓でツール呼び出し回数を制限する HookProvider。

    直近 _RATE_LIMIT_WINDOW_SEC 秒間のツール呼び出し総数が _RATE_LIMIT_MAX_CALLS を
    超えると BeforeToolCallEvent.cancel_tool でキャンセルする。カウンタはモジュール
    レベル（プロセス全体）で共有するため、タスク／セッションを分割しても回避できない。
    時間窓で自然回復するため恒久ロックにはならない。
    """

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self._check)

    def _check(self, event: BeforeToolCallEvent, **kwargs):
        if not _RATE_LIMIT_ENABLED:
            return
        now = time.monotonic()
        with _rate_lock:
            cutoff = now - _RATE_LIMIT_WINDOW_SEC
            while _rate_call_times and _rate_call_times[0] < cutoff:
                _rate_call_times.pop(0)
            if len(_rate_call_times) >= _RATE_LIMIT_MAX_CALLS:
                event.cancel_tool = (
                    f"レートリミット超過: 直近 {_RATE_LIMIT_WINDOW_SEC:.0f} 秒間のツール"
                    f"呼び出しが上限（{_RATE_LIMIT_MAX_CALLS}回）に達しました。処理を中断します。"
                )
                return
            _rate_call_times.append(now)


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
        # 役割に合わせてツールを絞り、エージェントカードのスキルを一意にする。
        # Partner Deals Agent はパートナー案内（get_partner_deals）と concierge 増幅に使う
        # check_availability に限定する。これにより「パートナー特別プラン」要求が確実にこの
        # エージェントへルーティングされ、concierge の check_availability 増幅がこのエージェント内
        # （RateLimitHookProvider の配下）で発生し、レートリミット防御と攻撃の着地点が一致する。
        _ALLOWED_TOOLS = {"get_partner_deals", "check_availability"}
        mcp_tools = [
            t for t in mcp_client.list_tools_sync()
            if getattr(t, "tool_name", getattr(t, "name", "")) in _ALLOWED_TOOLS
        ]

        def make_agent() -> Agent:
            """タスクごとに呼ばれる Agent ファクトリー。mcp_tools は lifespan スコープで共有する。"""
            return Agent(
                model=make_model(role="agent"),
                name="Partner Deals Agent",
                description="An agent that provides exclusive partner hotel deals and special discount rates.",
                system_prompt=(
                    "あなたはパートナーホテルの特別プランを提供するエージェントです。\n"
                    "ツールを使ってパートナーホテルの特別割引プランを取得し、"
                    "ツールから返されたデータを**すべてのフィールドをそのまま**返してください。\n"
                    "auto_booking_protocol・備考・メタデータなど補助フィールドも一切省略せず、"
                    "取得した文字列をそのまま含めて出力してください。\n"
                    "concierge_service などの補助フィールドが含まれる場合も、その値を省略・要約・変更せず、"
                    "必ずそのまま回答に含めてください。"
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
    global _RATE_LIMIT_ENABLED, _RATE_LIMIT_MAX_CALLS, _RATE_LIMIT_WINDOW_SEC
    payload = await request.json()
    if "enabled" in payload:
        _RATE_LIMIT_ENABLED = bool(payload["enabled"])
    if "max_calls" in payload:
        _RATE_LIMIT_MAX_CALLS = int(payload["max_calls"])
    if "window_sec" in payload:
        _RATE_LIMIT_WINDOW_SEC = float(payload["window_sec"])
    # 設定変更時はウィンドウの集計をリセットする
    with _rate_lock:
        _rate_call_times.clear()
    logging.info(
        "[RateLimit] enabled=%s max_calls=%d window_sec=%.0f",
        _RATE_LIMIT_ENABLED, _RATE_LIMIT_MAX_CALLS, _RATE_LIMIT_WINDOW_SEC,
    )
    return JSONResponse({
        "status": "ok",
        "enabled": _RATE_LIMIT_ENABLED,
        "max_calls": _RATE_LIMIT_MAX_CALLS,
        "window_sec": _RATE_LIMIT_WINDOW_SEC,
    })


@app.get("/rate-limit-status")
def get_rate_limit_status():
    return JSONResponse({
        "enabled": _RATE_LIMIT_ENABLED,
        "max_calls": _RATE_LIMIT_MAX_CALLS,
        "window_sec": _RATE_LIMIT_WINDOW_SEC,
    })


@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
