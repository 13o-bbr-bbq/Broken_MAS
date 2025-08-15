import os
import json
import uuid
import asyncio
import anyio
import logging
import httpx
from typing import Dict, Any

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_agentchat.agents import AssistantAgent as Agent
from autogen_ext.tools.mcp import mcp_server_tools, StreamableHttpServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.tools import FunctionTool

# A2A.
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import Client as A2AClient
from a2a.types import MessageSendParams, SendMessageRequest


logging.getLogger("autogen_core").setLevel(logging.WARNING)
logging.getLogger("autogen_core.events").setLevel(logging.WARNING)


def create_autogen_agent() -> Agent:
    # Create model client.
    model_client = OpenAIChatCompletionClient(
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key = os.getenv("OPENAI_API_KEY")
    )

    # Tool (MCP server) registration.
    find_params = StreamableHttpServerParams(url=os.getenv("MCP_FIND_RESTAURANT_URL"))
    detail_params = StreamableHttpServerParams(url=os.getenv("MCP_RESTAURANT_DETAIL_URL"))
    search_tools = asyncio.run(mcp_server_tools(find_params))
    detail_tools = asyncio.run(mcp_server_tools(detail_params))
    tools = search_tools + detail_tools

    # ---- A2A（a2a-sdk）: System1 の A2A（プロキシ）へ注文仕様を送るツール ----
    peer_base_url = os.getenv("PEER_A2A_1_URL")
    async def send_order_via_a2a(spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        System1 の A2A（a2a-sdk サーバ）へ 'message/send' を投げ、
        戻りの text パート(JSON文字列)を dict にして返す。
        """
        async with httpx.AsyncClient() as httpx_client:
            # 1) AgentCard を解決
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=peer_base_url)
            card = await resolver.get_agent_card()

            # 2) クライアント生成
            client = A2AClient(httpx_client=httpx_client, agent_card=card)

            # 3) 送信ペイロード（text パートに JSON 仕様をそのまま入れる）
            payload = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": json.dumps(spec, ensure_ascii=False)}],
                    "messageId": uuid.uuid4().hex,
                }
            }
            req = SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(**payload))

            # 4) 送信（非ストリーミング）
            resp = await client.send_message(req)

            # 5) 応答の text パートから JSON を抽出
            if getattr(resp, "result", None) and getattr(resp.result, "parts", None):
                for p in resp.result.parts:
                    if getattr(p, "kind", "") == "text":
                        text = getattr(p, "text", "")
                        if text:
                            try:
                                return json.loads(text)
                            except Exception:
                                return {"status": "error", "reason": "invalid JSON from A2A", "raw": text}
            return {"status": "error", "reason": "empty A2A response"}

    # FunctionTool は async 関数をそのまま登録可能
    a2a_tool = FunctionTool(
        name="send_order_via_a2a",
        func=send_order_via_a2a,
        description="Send order spec to System2 via System1's A2A and return order result."
    )
    tools.append(a2a_tool)

    # Create Agent.
    agent = Agent(
        name=os.getenv("A2A_1_AGENT_NAME", "System1Agent"),
        system_message=(
            "You are System1. "
            "1) use search_restaurants to find pizza shops; "
            "2) use get_restaurant_details to fetch A2A URL; "
            "3) build an order spec from user's wish/budget; "
            "4) call send_order_via_a2a(spec); "
            "5) present the result. "
            "必ず send_order_via_a2a を呼び出す前に JSON 形式の 'spec' を構築すること。"
            "spec は以下のキーを含む: task_id, requirements(wish, budget_jpy)"
        ),
        description="Restaurant finder + order spec builder",
        tools=tools,
        model_client=model_client
    )
    return agent


async def run_user_chat(agent: Agent) -> None:
    user_proxy = UserProxyAgent("user_proxy", input_func=input)
    team = RoundRobinGroupChat([agent, user_proxy])
    stream = team.run_stream(task="")
    await Console(stream)


def main() -> None:
    agent = create_autogen_agent()
    anyio.run(run_user_chat, agent)

if __name__ == "__main__":
    main()
