import os
import json
import uvicorn
from collections.abc import Mapping
from typing import Optional, Dict, Any, List
try:
    from pydantic import BaseModel
except Exception:
    BaseModel = None

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
    # 1) wish Partial match & Priority within budget.
    if wish:
        cand = [m for m in menu if wish in m["name"]]
        if budget_jpy is not None:
            cand = [x for x in cand if x["price"] <= budget_jpy]
        if cand:
            return sorted(cand, key=lambda x: x["price"])[-1]
    # 2) Maximum within budget.
    if budget_jpy is not None:
        under = [m for m in menu if m["price"] <= budget_jpy]
        if under:
            return sorted(under, key=lambda x: x["price"])[-1]
    # 3) cheapest
    return sorted(menu, key=lambda x: x["price"])[0] if menu else None


class PizzaOrderExecutor(AgentExecutor):
    """Read the ‘specification document (JSON)’ received from System1, place an order via MCP, and return the results."""
    def __init__(self):
        self.menu_client  = FastMCPClient(os.getenv("MCP_MENU_URL"))
        self.order_client = FastMCPClient(os.getenv("MCP_ORDER_URL"))

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Interpret the first text part of the received message as JSON.
        parts = getattr(context.message, "parts", []) or []
        print(f"System2: from System2's parts: {parts}, {type(parts)}")

        def unwrap(x):
            # Extract from "Part(root=...), RootModel(__root__/root=...)"
            if hasattr(x, "root"):
                return x.root
            if hasattr(x, "__root__"):
                return x.__root__
            return x

        text = None
        for p in parts:
            base = unwrap(p)
            if getattr(base, "kind", None) == "text":
                text = getattr(base, "text", None)
                if text:
                    break

        if text is None:
            raise ValueError("No text part found in message parts.")
        in_json = json.loads(text)
        print(f"System2: from System1's in_json: {in_json}, {type(in_json)}")

        # Extract order spec.
        task_id = in_json.get("task_id")
        requirements = in_json.get("requirements", in_json)
        wish   = requirements.get("wish")
        budget = requirements.get("budget_jpy")

        async with self.menu_client, self.order_client:
            # Basic server interaction.
            await self.menu_client.ping()
            await self.order_client.ping()

            # 1) Get menu.
            menu_res = await self.menu_client.call_tool("get_menu")
            items_model_list = getattr(menu_res.data, "items", None)
            print(f"System2: from MCP Server's items_model_list: {items_model_list}, {type(items_model_list)}")
            menu: list[dict] = []
            if isinstance(items_model_list, list):
                for it in items_model_list:
                    # Pydantic v2 / v1 の両対応
                    if hasattr(it, "model_dump"):
                        menu.append(it.model_dump())
                    elif hasattr(it, "dict"):
                        menu.append(it.dict())
                    else:
                        # それでもダメなら属性で手動展開
                        menu.append({
                            "name": getattr(it, "name", None),
                            "price": getattr(it, "price", None),
                            "description": getattr(it, "description", None),
                        })
            else:
                # フォールバック（辞書やRootModelで返ってきたとき）
                raw = menu_res.data
                if hasattr(raw, "model_dump"):
                    raw = raw.model_dump()
                elif hasattr(raw, "dict"):
                    raw = raw.dict()
                if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
                    menu = []
                    for it in raw["items"]:
                        if hasattr(it, "model_dump"):
                            menu.append(it.model_dump())
                        elif hasattr(it, "dict"):
                            menu.append(it.dict())
                        elif isinstance(it, dict):
                            menu.append(it)
            print(f"System2: from MCP Server's menu: {menu}, {type(menu)}")

            if not (isinstance(menu, list) and all(isinstance(x, dict) for x in menu)):
                payload = {
                    "task_id": task_id,
                    "status": "error",
                    "reason": f"Unexpected menu type: {type(menu).__name__}"
                }
                await event_queue.enqueue_event(new_agent_text_message(json.dumps(payload, ensure_ascii=False)))
                return

            # 2) Select item.
            print(f"[Order Spec] menu: {menu}, wish: {wish}, budget: {budget}")
            item = _choose_item(menu, wish, budget)
            print(f"System2: choosed item: {item}, {type(item)}")
            if not item:
                payload = {
                    "task_id": task_id,
                    "status": "no_match",
                    "reason": "No matching results found"
                }
            else:
                # Order.
                order_res = await self.order_client.call_tool(
                    "place_order",
                    {"item_name": item["name"], "price": int(item["price"])}
                )
                order = order_res.data
                if hasattr(order, "model_dump"):
                    order = order.model_dump()
                elif hasattr(order, "dict"):
                    order = order.dict()
                elif not isinstance(order, dict) and getattr(order_res, "content", None):
                    # サーバー実装が text で返す場合のフォールバック
                    for c in order_res.content:
                        if getattr(c, "text", None):
                            try:
                                order = json.loads(c.text)
                            except Exception:
                                order = {"ordered_item": str(c.text)}
                            break

                print(f"Order: {order}")
                payload = {
                    "task_id": task_id,
                    "status": "confirmed",
                    "ordered_item": order["ordered_item"],
                    "price": order["price"],
                    "estimated_delivery": order["estimated_delivery"],
                }

            # Return the response as an A2A message (the body is a JSON string).
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(payload, ensure_ascii=False)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="process_order_spec",
        name="Process order spec",
        description="Receive order spec (JSON) and place an order via MCP.",
        tags=["order", "pizza", "spec"],
        examples=['{"task_id":"t-123","requirements":{"wish":"margherita","budget_jpy":2000}}'],
    )
    return AgentCard(
        name=os.getenv("A2A_AGENT_2_NAME", "System2A2A"),
        description="A2A interface for System2 (Broken Pizza Shop)",
        url=os.getenv("A2A_2_SERVER"),
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main():
    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=DefaultRequestHandler(
            agent_executor=PizzaOrderExecutor(),
            task_store=InMemoryTaskStore(),
        ),
    )
    uvicorn.run(server.build(), host="0.0.0.0", port=int(os.getenv("A2A_2_PORT", "9000")))

if __name__ == "__main__":
    main()
