import os
import json
import uvicorn
from typing import Optional, Dict, Any, List

# MCP.
from fastmcp import Client as FastMCPClient

# A2A.
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCard, AgentSkill, AgentCapabilities
from a2a.utils.message import new_agent_text_message


def _choose_item(menu: List[Dict[str, Any]], wish: Optional[str], budget_jpy: Optional[int]) -> Optional[Dict[str, Any]]:
    # 1) wish 部分一致 & 予算内を優先
    if wish:
        cand = [m for m in menu if wish in m["name"]]
        if budget_jpy is not None:
            cand = [x for x in cand if x["price"] <= budget_jpy]
        if cand:
            return sorted(cand, key=lambda x: x["price"])[-1]
    # 2) 予算内最大
    if budget_jpy is not None:
        under = [m for m in menu if m["price"] <= budget_jpy]
        if under:
            return sorted(under, key=lambda x: x["price"])[-1]
    # 3) 最安
    return sorted(menu, key=lambda x: x["price"])[0] if menu else None


class PizzaOrderExecutor(AgentExecutor):
    """System1 から届く '仕様書(JSON)' を読み取り、MCP 経由で注文して結果を返す。"""
    def __init__(self):
        self.menu_url  = os.getenv("MCP_MENU_URL")
        self.order_url = os.getenv("MCP_ORDER_URL")
        if not self.menu_url or not self.order_url:
            raise RuntimeError("MCP endpoints not configured")

        self.menu_client  = FastMCPClient(self.menu_url)
        self.order_client = FastMCPClient(self.order_url)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 受信メッセージの最初の text パートを JSON として解釈
        req_json: Dict[str, Any] = {}
        try:
            parts = getattr(context.message, "parts", []) or []
            text_parts = [p for p in parts if getattr(p, "kind", "") == "text"]
            if text_parts:
                req_json = json.loads(getattr(text_parts[0], "text", "{}"))
        except Exception:
            pass

        task_id = req_json.get("task_id")
        requirements = req_json.get("requirements", req_json)

        wish   = requirements.get("wish")
        budget = requirements.get("budget_jpy")

        # MCP: メニュー取得 → 商品選択 → 注文
        menu = self.menu_client.invoke_tool("get_menu")
        item = _choose_item(menu, wish, budget)
        if not item:
            payload = {"task_id": task_id, "status": "no_match", "reason": "条件に合致なし"}
        else:
            order = self.order_client.invoke_tool("place_order", item_name=item["name"], price=item["price"])
            payload = {
                "task_id": task_id,
                "status": "confirmed",
                "ordered_item": order["ordered_item"],
                "price": order["price"],
                "estimated_delivery": order["estimated_delivery"],
            }

        # 応答は A2A の Message として返す（本文はJSON文字列）
        await event_queue.enqueue_event(new_agent_text_message(json.dumps(payload, ensure_ascii=False)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_agent_card(base_url: str) -> AgentCard:
    skill = AgentSkill(
        id="process_order_spec",
        name="Process order spec",
        description="Receive order spec (JSON) and place an order via MCP.",
        tags=["order", "pizza", "spec"],
        examples=['{"task_id":"t-123","requirements":{"wish":"マルゲリータ","budget_jpy":2000}}'],
    )
    return AgentCard(
        name=os.getenv("A2A_AGENT_2_NAME", "System2A2A"),
        description="A2A interface for System2 (Broken Pizza Shop)",
        url=base_url,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main():
    port = int(os.getenv("A2A_2_PORT", "9000"))
    base_url = f"http://localhost:{port}/"  # 代理公開用URL（本番は外部公開URLに合わせる）

    server = A2AStarletteApplication(
        agent_card=build_agent_card(base_url),
        http_handler=DefaultRequestHandler(
            agent_executor=PizzaOrderExecutor(),
            task_store=InMemoryTaskStore(),
        ),
    )
    uvicorn.run(server.build(), host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
