import os
import json
import uuid
import uvicorn
import httpx
from typing import Dict, Any

# A2A.
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    AgentCard, AgentSkill, AgentCapabilities,
    MessageSendParams, SendMessageRequest,
)
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import Client as A2AClient
from a2a.utils.message import new_agent_text_message


class OrderProxyExecutor(AgentExecutor):
    """受け取った仕様書(JSON)を System2 の A2A に転送し、応答をそのまま返す。"""

    def __init__(self, peer_base_url: str):
        self.peer_base_url = peer_base_url

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # System1への入力（textパートJSON）をそのまま System2 へ転送
        in_json = {}
        try:
            parts = getattr(context.message, "parts", []) or []
            text_parts = [p for p in parts if getattr(p, "kind", "") == "text"]
            if text_parts:
                in_json = json.loads(getattr(text_parts[0], "text", "{}"))
        except Exception:
            pass

        # System2 の AgentCard を解決し、message/send を実行
        async with httpx.AsyncClient() as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.peer_base_url)
            system2_card = await resolver.get_agent_card()  # /.well-known/agent-card.json 取得
            client = A2AClient(httpx_client=httpx_client, agent_card=system2_card)

            payload = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": json.dumps(in_json, ensure_ascii=False)}],
                    "messageId": uuid.uuid4().hex,
                }
            }
            request = SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(**payload))
            response = await client.send_message(request)  # 非ストリーミング応答

        # 応答（Message結果）の最初の text をそのまま返す（= System2のJSON文字列）
        result_json_text = ""
        if getattr(response, "result", None) and getattr(response.result, "parts", None):
            parts = response.result.parts
            for p in parts:
                if getattr(p, "kind", "") == "text":
                    result_json_text = getattr(p, "text", "")
                    break

        await event_queue.enqueue_event(new_agent_text_message(result_json_text))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_agent_card(base_url: str) -> AgentCard:
    skill = AgentSkill(
        id="send_order_spec_to_system2",
        name="Send order spec to System2",
        description="Proxy: forward order spec JSON to System2 A2A and return the result.",
        tags=["proxy", "order"],
        examples=['{"task_id":"t-123","requirements":{"wish":"マルゲリータ","budget_jpy":2000}}'],
    )
    return AgentCard(
        name=os.getenv("A2A_1_AGENT_NAME", "System1A2A"),
        description="A2A interface for System1 (proxy to System2)",
        url=base_url,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main():
    port = int(os.getenv("A2A_1_PORT", "9000"))
    base_url = f"http://localhost:{port}/"
    peer_url = os.getenv("PEER_A2A_2_URL")

    server = A2AStarletteApplication(
        agent_card=build_agent_card(base_url),
        http_handler=DefaultRequestHandler(
            agent_executor=OrderProxyExecutor(peer_base_url=peer_url),
            task_store=InMemoryTaskStore(),
        ),
    )
    uvicorn.run(server.build(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
